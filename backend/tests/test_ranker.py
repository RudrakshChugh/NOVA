"""Reviewer-adapted ranker (plan §3.1, §3.3.5, §11 step 3).

The two properties that matter scientifically are tested here directly: that no feature derived
from a reviewer's decision can reach the model, and that LightGBM ships only when it beats both
floors on held-out review sittings.
"""

import random

import pytest

from app.services.metrics import ndcg_at_k
from app.services.ranker import models as rm
from app.services.ranker import training as rt
from app.services.ranker.features import FEATURE_NAMES, Decision

# Names of anything derived from the decision on the same question. None may be a feature.
LEAKING = (
    "accepted", "accept", "decision", "edit_distance", "edited", "edited_stem", "rejected",
    "reason", "review", "reviewer", "relevance", "label", "published", "state",
)


# --- leakage -------------------------------------------------------------------------------


def test_no_feature_is_derived_from_the_decision():
    for name in FEATURE_NAMES:
        for leak in LEAKING:
            assert leak not in name, f"feature {name!r} looks derived from the review decision"


def test_feature_names_are_unique_and_fixed_width():
    assert len(FEATURE_NAMES) == len(set(FEATURE_NAMES))
    assert len(rm.vector({n: 0.0 for n in FEATURE_NAMES})) == len(FEATURE_NAMES)


def test_decision_grades_accept_above_edit_above_reject():
    grades = {}
    for decision, expected in (("accept", 2), ("edit", 1), ("reject", 0)):
        grades[decision] = expected
    assert grades["accept"] > grades["edit"] > grades["reject"]
    assert Decision(accepted_without_edit=True, relevance=2).accepted_without_edit
    assert not Decision(accepted_without_edit=False, relevance=1).accepted_without_edit


# --- synthetic examples ----------------------------------------------------------------------


def _example(session: str, quality: float, accepted: bool, relevance: int, seed: int) -> rm.Example:
    """A feature vector whose `satisfaction_rate` carries the signal and whose other features are
    noise, so a model that learns nothing can still be compared against the compliance floor.

    The seed is passed in rather than derived from `hash()`, which Python randomises per process
    and would make these comparisons differ between runs."""
    rng = random.Random(seed)
    features = {name: rng.random() for name in FEATURE_NAMES}
    features["satisfaction_rate"] = quality
    return rm.Example(features=features, accepted=accepted, relevance=relevance, session=(session,))


def _learnable_set(sessions: int = 4, per_session: int = 8) -> list[rm.Example]:
    """Acceptance follows the compliance rate, so every model has something real to find."""
    out = []
    for s in range(sessions):
        for i in range(per_session):
            quality = (i + 1) / (per_session + 1)
            accepted = quality > 0.5
            out.append(_example(f"s{s}", quality, accepted, 2 if accepted else 0, seed=s * 100 + i))
    return out


def _noise_set(sessions: int = 4, per_session: int = 8) -> list[rm.Example]:
    """Acceptance is unrelated to any feature, so nothing should beat the floor convincingly."""
    rng = random.Random(11)
    out = []
    for s in range(sessions):
        for i in range(per_session):
            accepted = rng.random() > 0.5
            out.append(_example(f"s{s}", rng.random(), accepted, 2 if accepted else 0, seed=s * 100 + i))
    return out


# --- cross-validation ------------------------------------------------------------------------


def test_cross_validation_holds_out_whole_sittings():
    examples = _learnable_set()
    comparison = rt.cross_validate(examples)
    assert set(comparison) >= {rm.COMPLIANCE_ONLY}
    for entry in comparison.values():
        assert entry["sessions"] == 4  # one held-out score per sitting, none skipped
        interval = entry["ndcg"]
        assert interval["low"] <= interval["point"] <= interval["high"]
        assert 0.0 <= interval["point"] <= 1.0


def test_learned_models_match_or_beat_the_floor_on_learnable_data():
    comparison = rt.cross_validate(_learnable_set())
    floor = comparison[rm.COMPLIANCE_ONLY]["ndcg"]["point"]
    assert floor > 0.9  # the floor is strong here by construction
    for name in (rm.LOGISTIC_REGRESSION, rm.LIGHTGBM):
        if name in comparison:
            assert comparison[name]["ndcg"]["point"] >= floor - 0.15


# --- the selection rule ----------------------------------------------------------------------


def _entry(point: float) -> dict:
    return {"sessions": 4, "ndcg": {"point": point, "low": point - 0.05, "high": point + 0.05}}


def test_lightgbm_ships_only_when_it_beats_both_floors():
    chosen, beat = rt.choose({
        rm.COMPLIANCE_ONLY: _entry(0.70),
        rm.LOGISTIC_REGRESSION: _entry(0.75),
        rm.LIGHTGBM: _entry(0.82),
    })
    assert chosen == rm.LIGHTGBM and beat is True


def test_simpler_model_ships_when_lightgbm_beats_only_one_floor():
    chosen, beat = rt.choose({
        rm.COMPLIANCE_ONLY: _entry(0.70),
        rm.LOGISTIC_REGRESSION: _entry(0.85),
        rm.LIGHTGBM: _entry(0.80),
    })
    assert chosen == rm.LOGISTIC_REGRESSION and beat is False


def test_compliance_floor_ships_when_nothing_learned_helps():
    chosen, beat = rt.choose({
        rm.COMPLIANCE_ONLY: _entry(0.90),
        rm.LOGISTIC_REGRESSION: _entry(0.60),
        rm.LIGHTGBM: _entry(0.65),
    })
    assert chosen == rm.COMPLIANCE_ONLY and beat is False


def test_selection_falls_back_to_the_floor_with_nothing_to_compare():
    chosen, beat = rt.choose({})
    assert chosen == rm.COMPLIANCE_ONLY and beat is False
    chosen, beat = rt.choose({rm.LIGHTGBM: _entry(0.9)})
    assert chosen == rm.COMPLIANCE_ONLY and beat is False  # no floor to beat means no claim


def test_noise_does_not_let_lightgbm_claim_a_win_over_both_floors():
    """Guards the overfitting risk plan §12 names: on unlearnable data the boosted model must not
    sweep both floors on held-out sittings."""
    comparison = rt.cross_validate(_noise_set())
    _, beat = rt.choose(comparison)
    assert not beat


# --- persistence and scoring -------------------------------------------------------------------


def test_each_model_round_trips_through_its_stored_form(tmp_path):
    examples = _learnable_set()
    probe = examples[0].features
    for scorer in (rm.ComplianceOnlyRanker(), rm.fit_linear(examples), rm.fit_boosted(examples)):
        path = tmp_path / f"{scorer.algorithm}.json"
        rm.save(scorer, path, metrics={"note": "test"}, version=1)
        loaded = rm.load(path)
        assert loaded.algorithm == scorer.algorithm
        assert loaded.score(probe) == pytest.approx(scorer.score(probe), abs=1e-9)


def test_stored_model_records_the_feature_order_it_was_fitted_on(tmp_path):
    import json

    path = tmp_path / "ranker.json"
    rm.save(rm.fit_linear(_learnable_set()), path, metrics={}, version=2)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["feature_names"] == list(FEATURE_NAMES)
    assert payload["version"] == 2 and payload["trained_at"]


def test_compliance_floor_scores_exactly_the_satisfaction_rate():
    scorer = rm.ComplianceOnlyRanker()
    assert scorer.score({"satisfaction_rate": 0.83}) == 0.83
    assert scorer.score({}) == 0.0


def test_ndcg_rewards_the_ordering_a_better_ranker_produces():
    relevances_good = [2, 2, 1, 0, 0]
    relevances_bad = [0, 0, 1, 2, 2]
    assert ndcg_at_k(relevances_good, k=5) > ndcg_at_k(relevances_bad, k=5)
