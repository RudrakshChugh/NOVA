"""Fitting and reporting the novelty combiner, under the protocol of plan §5.

`train` sees the train and validation folds only. `evaluate_test` is a separate, deliberate call
because the test fold is meant to be touched exactly once; every access is appended to the model's
record so a report can state how many times it actually was.
"""

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...models import ModelVersion
from . import active_combiner_path, dataset
from .combiner import NoveltyCombiner, evaluate, fit, single_signal_baseline

SINGLE_SIGNAL_BASELINE_THRESHOLD = 0.80  # the "one embedding threshold" plan §3.1 must be beaten


def _next_version(db: Session, kind: str) -> int:
    return (db.scalar(select(func.max(ModelVersion.version)).where(ModelVersion.kind == kind)) or 0) + 1


def train(db: Session, course_id: UUID) -> dict:
    """Fit on the train fold, select C and threshold on validation, activate the result."""
    train_samples = dataset.samples(db, course_id, "train")
    val_samples = dataset.samples(db, course_id, "val")
    if len(train_samples) < 10 or len(val_samples) < 5:
        return {
            "trained": False,
            "reason": f"not enough labelled pairs (train={len(train_samples)}, validation={len(val_samples)})",
        }

    version = _next_version(db, "novelty")
    combiner = fit(train_samples, val_samples, version)
    combiner.save(active_combiner_path())

    db.query(ModelVersion).filter_by(kind="novelty", is_active=True).update({"is_active": False})
    row = ModelVersion(
        kind="novelty", algorithm="logistic_regression", version=version,
        params={**combiner.params, "coefficients": combiner.coefficients, "intercept": combiner.intercept,
                "threshold": combiner.threshold},
        metrics={"validation": combiner.metrics, "test_evaluations": []},
        artifact_path=str(active_combiner_path()), is_active=True,
    )
    db.add(row)
    db.commit()
    return {"trained": True, **model_card(db)}


def evaluate_test(db: Session, course_id: UUID) -> dict:
    """Held-out performance of the active combiner, against the single-threshold baseline."""
    row = db.scalar(select(ModelVersion).where(ModelVersion.kind == "novelty", ModelVersion.is_active))
    if row is None or not active_combiner_path().exists():
        return {"evaluated": False, "reason": "no fitted novelty combiner"}
    test_samples = dataset.samples(db, course_id, "test")
    if not test_samples:
        return {"evaluated": False, "reason": "test fold is empty"}

    combiner = NoveltyCombiner.load(active_combiner_path())
    result = {
        "model": evaluate(combiner, test_samples),
        "single_signal_baseline": single_signal_baseline(
            test_samples, "semantic", SINGLE_SIGNAL_BASELINE_THRESHOLD
        ),
        "at": datetime.now(timezone.utc).isoformat(),
    }
    metrics = dict(row.metrics or {})
    accesses = list(metrics.get("test_evaluations", []))
    accesses.append(result)
    metrics["test_evaluations"] = accesses
    row.metrics = metrics
    db.commit()
    return {
        "evaluated": True,
        "test_fold_accesses": len(accesses),
        "protocol_note": (
            "plan §5 reserves the test fold for a single reported evaluation"
            if len(accesses) > 1
            else "first access"
        ),
        **result,
    }


def model_card(db: Session) -> dict:
    """What the active combiner is, in the terms plan §3.1 asks to be reportable."""
    path = active_combiner_path()
    combiner = NoveltyCombiner.load(path) if path.exists() else NoveltyCombiner.untrained()
    row = db.scalar(select(ModelVersion).where(ModelVersion.kind == "novelty", ModelVersion.is_active))
    return {
        "version": combiner.version,
        "is_fitted": combiner.is_fitted,
        "algorithm": "logistic_regression" if combiner.is_fitted else "equal-weight mean (placeholder)",
        "coefficients": combiner.coefficients,
        "intercept": combiner.intercept,
        "threshold": combiner.threshold,
        "params": combiner.params,
        "validation": combiner.metrics,
        "test_evaluations": (row.metrics or {}).get("test_evaluations", []) if row else [],
        "trained_at": combiner.trained_at,
    }
