"""Every reported number of plan §3.3.6, computed from what the study actually recorded.

Rules this module keeps:
  * a proportion carries a Wilson interval, a mean carries a bootstrap interval;
  * McNemar's test is reported with its effect size and that effect's interval, never alone;
  * where the confidence interval on a difference includes zero, the verdict says so rather than
    claiming an improvement;
  * the ranker ablation is judged on NDCG@k, because it changes candidate order and not which
    candidates survive — acceptance without edits cannot see a difference it makes.
"""

from statistics import pstdev
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models import CandidateQuestion, GenerationRun, GroundingCitation, ReviewRecord
from ..metrics import Interval, bootstrap_ci, cohens_kappa, mcnemar, ndcg_at_k, wilson_interval
from .arms import HEADLINE_COMPARISON, ORDER_ONLY_ARMS, get
from .harness import study_runs

CITATION_JUDGEMENT_SAMPLE = 50  # plan §3.3.6 asks a human judge to confirm this many


def _latest_review(candidate: CandidateQuestion) -> ReviewRecord | None:
    return max(candidate.reviews, key=lambda r: r.reviewed_at) if candidate.reviews else None


def _candidates(db: Session, blueprint_id: UUID) -> list[CandidateQuestion]:
    runs = study_runs(db, blueprint_id)
    return [c for run in runs for c in run.candidates]


def _by_arm(candidates: list[CandidateQuestion]) -> dict[str, list[CandidateQuestion]]:
    out: dict[str, list[CandidateQuestion]] = {}
    for c in candidates:
        out.setdefault(c.arm_label or "unlabelled", []).append(c)
    return out


# --- per-arm figures -------------------------------------------------------------------------


def _acceptance(candidates: list[CandidateQuestion]) -> dict:
    """The headline metric: of the candidates a reviewer judged, how many were accepted as-is."""
    reviewed = [(c, _latest_review(c)) for c in candidates]
    reviewed = [(c, r) for c, r in reviewed if r is not None]
    accepted = sum(1 for _, r in reviewed if r.decision == "accept")
    return {
        "reviewed": len(reviewed),
        "accepted_without_edits": accepted,
        "rate": wilson_interval(accepted, len(reviewed)).as_dict(),
    }


def _first_draft_compliance(candidates: list[CandidateQuestion]) -> dict:
    """Constraint satisfaction on the *first* draft, before any repair (plan §3.3.6)."""
    firsts = [c.reports[0] for c in candidates if c.reports]
    compliant = sum(1 for r in firsts if r.is_compliant)
    rates = [r.satisfaction_rate for r in firsts]
    return {
        "drafts": len(firsts),
        "fully_compliant": compliant,
        "rate": wilson_interval(compliant, len(firsts)).as_dict(),
        "mean_satisfaction": bootstrap_ci(rates, lambda s: sum(s) / len(s) if s else None).as_dict(),
    }


def _duplicate_rate(candidates: list[CandidateQuestion]) -> dict:
    audited = [c.novelty_reports[-1] for c in candidates if c.novelty_reports]
    duplicates = sum(1 for r in audited if r.is_duplicate)
    return {
        "audited": len(audited),
        "flagged_duplicate": duplicates,
        "rate": wilson_interval(duplicates, len(audited)).as_dict(),
    }


def _edit_distance(candidates: list[CandidateQuestion]) -> dict:
    """Mean normalised Levenshtein on the questions that were edited rather than accepted or
    rejected, with its standard deviation (plan §3.3.6)."""
    distances = [
        r.edit_distance
        for c in candidates
        if (r := _latest_review(c)) is not None and r.decision == "edit" and r.edit_distance is not None
    ]
    return {
        "edited": len(distances),
        "mean": bootstrap_ci(distances, lambda s: sum(s) / len(s) if s else None).as_dict(),
        "stdev": pstdev(distances) if len(distances) > 1 else None,
    }


def _per_seed_acceptance(candidates: list[CandidateQuestion]) -> dict:
    """Acceptance per seed, so generation noise is visible rather than averaged away."""
    by_seed: dict[int, list[CandidateQuestion]] = {}
    for c in candidates:
        by_seed.setdefault(c.run.seed, []).append(c)
    return {
        str(seed): _acceptance(rows)["rate"]["point"]
        for seed, rows in sorted(by_seed.items())
        if any(_latest_review(c) for c in rows)
    }


# --- paired comparison -----------------------------------------------------------------------


def _pair_key(candidate: CandidateQuestion) -> tuple:
    """Two arms produced comparable questions when they answered the same constraint under the
    same seed."""
    return (str(candidate.constraint_id), candidate.run.seed)


def headline(db: Session, blueprint_id: UUID) -> dict:
    """Full system against the fixed-prompt RAG baseline on acceptance without edits."""
    system_label, baseline_label = HEADLINE_COMPARISON
    grouped = _by_arm(_candidates(db, blueprint_id))
    system = {_pair_key(c): c for c in grouped.get(system_label, [])}
    baseline = {_pair_key(c): c for c in grouped.get(baseline_label, [])}

    paired = []
    for key in sorted(set(system) & set(baseline)):
        a, b = _latest_review(system[key]), _latest_review(baseline[key])
        if a is None or b is None:
            continue
        paired.append((a.decision == "accept", b.decision == "accept"))

    if not paired:
        return {
            "comparison": f"{system_label} vs {baseline_label}",
            "paired_questions": 0,
            "verdict": "not computed: no constraint has been reviewed under both arms",
        }

    result = mcnemar(paired)
    low, high = result.difference.low, result.difference.high
    if low > 0:
        verdict = "the full system was accepted without edits more often; the interval excludes zero"
    elif high < 0:
        verdict = "the baseline was accepted without edits more often; the interval excludes zero"
    else:
        verdict = "no significant difference: the interval on the difference includes zero"

    return {
        "comparison": f"{system_label} vs {baseline_label}",
        "paired_questions": len(paired),
        "mcnemar": result.as_dict(),
        "verdict": verdict,
    }


def ranker_ablation(db: Session, blueprint_id: UUID) -> dict:
    """NDCG@k over reviewer decisions for the arms that differ only in candidate order."""
    grouped = _by_arm(_candidates(db, blueprint_id))
    grades = {"accept": 2, "edit": 1, "reject": 0}
    out: dict[str, dict] = {}
    for label, candidates in grouped.items():
        by_run: dict[UUID, list[CandidateQuestion]] = {}
        for c in candidates:
            by_run.setdefault(c.run_id, []).append(c)
        scores = []
        for rows in by_run.values():
            reviewed = [(c, _latest_review(c)) for c in rows]
            reviewed = [(c, r) for c, r in reviewed if r is not None]
            if len(reviewed) < 2:
                continue
            # rows arrive in the order the platform served them, which is what is being judged
            value = ndcg_at_k([grades[r.decision] for _, r in reviewed], k=len(reviewed))
            if value is not None:
                scores.append(value)
        if scores:
            out[label] = {
                "runs": len(scores),
                "ndcg": bootstrap_ci(scores, lambda s: sum(s) / len(s) if s else None).as_dict(),
                "order_only_arm": label in ORDER_ONLY_ARMS,
            }
    return out


def inter_rater(db: Session, blueprint_id: UUID) -> dict:
    """Cohen's kappa on the candidates two reviewers both judged (plan §3.3.6)."""
    pairs: list[tuple[str, str]] = []
    for candidate in _candidates(db, blueprint_id):
        by_reviewer: dict[str, str] = {}
        for record in sorted(candidate.reviews, key=lambda r: r.reviewed_at):
            by_reviewer[str(record.reviewer_id)] = record.decision
        if len(by_reviewer) >= 2:
            first, second = list(by_reviewer.values())[:2]
            pairs.append((first, second))

    if len(pairs) < 2:
        return {"shared_questions": len(pairs), "kappa": None,
                "note": "needs candidates judged by two reviewers"}

    kappa = cohens_kappa(pairs)
    interval = bootstrap_ci(pairs, cohens_kappa)
    return {
        "shared_questions": len(pairs),
        "kappa": interval.as_dict(),
        "weakly_grounded": kappa is not None and kappa < 0.4,
        "note": (
            "kappa below 0.4: plan §3.3.6 requires the acceptance metric to be flagged as weakly grounded"
            if kappa is not None and kappa < 0.4
            else "agreement is adequate for the acceptance metric"
        ),
    }


def grounding(db: Session, blueprint_id: UUID) -> dict:
    """What can be checked automatically — whether a cited quote is really in the passage — plus a
    random sample for the human judgement plan §3.3.6 asks for."""
    import random

    candidates = _candidates(db, blueprint_id)
    citations = [c for cand in candidates for c in cand.citations]
    verbatim = sum(1 for c in citations if c.char_span != "not_found")

    rng = random.Random(20260915)
    sample = rng.sample(citations, min(CITATION_JUDGEMENT_SAMPLE, len(citations))) if citations else []
    return {
        "citations": len(citations),
        "quote_found_verbatim": wilson_interval(verbatim, len(citations)).as_dict(),
        "human_judgement_sample": [
            {
                "citation_id": str(c.citation_id),
                "question": c.question.stem,
                "page": c.chunk.page_ref,
                "passage": c.chunk.text,
                "support_score": c.support_score,
                "quote_found_verbatim": c.char_span != "not_found",
            }
            for c in sample
        ],
        "note": (
            f"{len(sample)} citations sampled for a human judge to confirm they support the claim; "
            "the verbatim rate above is machine-checked and is not a substitute for that judgement"
        ),
    }


def build(db: Session, blueprint_id: UUID) -> dict:
    """The full evaluation report for one study."""
    candidates = _candidates(db, blueprint_id)
    grouped = _by_arm(candidates)
    arms = {
        label: {
            "kind": get(label).kind,
            "description": get(label).description,
            "candidates": len(rows),
            "acceptance_without_edits": _acceptance(rows),
            "first_draft_compliance": _first_draft_compliance(rows),
            "novelty": _duplicate_rate(rows),
            "edit_distance": _edit_distance(rows),
            "acceptance_by_seed": _per_seed_acceptance(rows),
        }
        for label, rows in sorted(grouped.items())
    }
    return {
        "blueprint_id": str(blueprint_id),
        "candidates": len(candidates),
        "reviewed": sum(1 for c in candidates if c.reviews),
        "arms": arms,
        "headline": headline(db, blueprint_id),
        "ranker_ablation": ranker_ablation(db, blueprint_id),
        "inter_rater_agreement": inter_rater(db, blueprint_id),
        "grounding": grounding(db, blueprint_id),
        "protocol": {
            "bootstrap_resamples": 10000,
            "proportion_intervals": "Wilson score",
            "pairing": "same constraint, same seed",
            "note": "no p-value is reported without its effect size and interval (plan §3.3.6)",
        },
    }
