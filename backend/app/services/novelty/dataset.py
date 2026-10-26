"""Building, splitting and vectorising the labelled pair set (plan §5, §11 step 1)."""

import json
import random
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models import PYQ, DuplicatePair, PYQPaper
from ..embeddings import embed
from .combiner import Sample
from .pairs import SPLIT_SEED, assign_split, build_negatives, build_positives, composition, dedupe, pair_key
from .signals import BM25Index, Metadata, signal_vector

SPLIT_MANIFEST = Path(__file__).resolve().parents[3] / "data" / "novelty_split.json"


def _corpus(db: Session, course_id: UUID) -> list[PYQ]:
    return list(
        db.scalars(
            select(PYQ).join(PYQPaper, PYQPaper.paper_id == PYQ.paper_id).where(PYQPaper.course_id == course_id)
        ).all()
    )


def _neighbours(questions: list[PYQ], k: int) -> dict[UUID, list[tuple[UUID, float]]]:
    """Most semantically similar other question per question — the hard-negative candidates."""
    out: dict[UUID, list[tuple[UUID, float]]] = {}
    vectors = {q.pyq_id: list(q.embedding) for q in questions}
    for q in questions:
        scores = [
            (other.pyq_id, sum(a * b for a, b in zip(vectors[q.pyq_id], vectors[other.pyq_id])))
            for other in questions
            if other.pyq_id != q.pyq_id
        ]
        scores.sort(key=lambda row: row[1], reverse=True)
        out[q.pyq_id] = scores[:k]
    return out


def build_pair_set(
    db: Session,
    course_id: UUID,
    positives_per_question: int = 2,
    hard_negatives_per_question: int = 1,
    easy_negatives_per_question: int = 1,
    seed: int = SPLIT_SEED,
) -> dict:
    """Construct pairs from the course's past-year corpus and store the ones that are new.
    Existing pairs keep their fold: assignment is a function of the pair key and the seed."""
    questions = _corpus(db, course_id)
    if len(questions) < 2:
        return {"added": 0, "skipped_existing": 0, "total": 0, "error": "need at least two past-year questions"}

    rng = random.Random(seed)
    pairs_in = [(q.pyq_id, q.text) for q in questions]
    neighbours = _neighbours(questions, k=max(1, hard_negatives_per_question))
    constructed = dedupe(
        build_positives(pairs_in, rng, positives_per_question)
        + build_negatives(pairs_in, neighbours, rng, hard_negatives_per_question, easy_negatives_per_question)
    )

    existing = set(db.scalars(select(DuplicatePair.pair_key)).all())
    added = 0
    for p in constructed:
        key = pair_key(p.left_text, p.right_text)
        if key in existing:
            continue
        existing.add(key)
        db.add(
            DuplicatePair(
                pair_key=key, left_text=p.left_text, right_text=p.right_text,
                left_pyq_id=p.left_pyq_id, right_pyq_id=p.right_pyq_id,
                label=p.label, stratum=p.stratum, provenance=p.provenance, split=assign_split(key, seed),
            )
        )
        added += 1
    db.commit()
    return {
        "added": added,
        "skipped_existing": len(constructed) - added,
        **stats(db),
    }


def stats(db: Session) -> dict:
    rows = db.execute(select(DuplicatePair.split, DuplicatePair.stratum, DuplicatePair.label)).all()
    return {
        "total": len(rows),
        "composition": composition([(r[0], r[1], r[2]) for r in rows]),
        "seed": SPLIT_SEED,
    }


def export_split_manifest(db: Session, path: Path = SPLIT_MANIFEST) -> dict:
    """Write the committed split file: the seed, the assignment rule, and every pair's fold.
    Regenerating it can only reproduce the same assignment — the rule depends on nothing else."""
    rows = db.execute(
        select(DuplicatePair.pair_key, DuplicatePair.split, DuplicatePair.stratum, DuplicatePair.label)
        .order_by(DuplicatePair.pair_key)
    ).all()
    manifest = {
        "seed": SPLIT_SEED,
        "rule": "sha256(f'{seed}:{pair_key}')[:8] / 2**64 -> [0,0.6) train, [0.6,0.8) val, [0.8,1) test",
        "fractions": {"train": 0.6, "val": 0.2, "test": 0.2},
        "composition": composition([(r[1], r[2], r[3]) for r in rows]),
        "pairs": {r[0]: r[1] for r in rows},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return {"path": str(path), "pairs": len(rows), "composition": manifest["composition"]}


def samples(db: Session, course_id: UUID, split: str) -> list[Sample]:
    """Signal vectors for every pair in one fold. IDF and average document length come from the
    course's past-year corpus, so a pair is scored exactly as a live candidate would be."""
    rows = list(db.scalars(select(DuplicatePair).where(DuplicatePair.split == split)).all())
    if not rows:
        return []
    corpus = _corpus(db, course_id)
    index = BM25Index({str(q.pyq_id): q.text for q in corpus})
    meta_by_id = {q.pyq_id: Metadata(q.question_type, q.marks, q.bloom_level) for q in corpus}

    texts = [t for row in rows for t in (row.left_text, row.right_text)]
    vectors = embed(texts)
    out: list[Sample] = []
    for i, row in enumerate(rows):
        left_vec, right_vec = vectors[2 * i], vectors[2 * i + 1]
        left_meta = meta_by_id.get(row.left_pyq_id, Metadata())
        # A constructed variant inherits the metadata of the question it was derived from.
        right_meta = meta_by_id.get(row.right_pyq_id, left_meta if row.label else Metadata())
        out.append(
            (
                signal_vector(
                    left_text=row.left_text, right_text=row.right_text,
                    left_vec=left_vec, right_vec=right_vec,
                    left_meta=left_meta, right_meta=right_meta, index=index,
                ),
                row.label,
            )
        )
    return out
