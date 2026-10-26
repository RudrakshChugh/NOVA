"""Driving the baseline and ablation runs (plan §3.3.2, §3.3.3, §3.3.5, §11 step 6).

A study is every run of one frozen blueprint that carries an arm label. Each arm is run once per
seed, and the *same* seed list is used for every arm, so a candidate from one arm pairs exactly
with the candidate another arm produced for the same constraint under the same seed. That pairing
is what McNemar's test needs, and it is also what keeps the comparison from being confounded by
generation noise.
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models import Blueprint, GenerationRun
from .. import orchestrator
from .arms import ARMS, Arm, get

DEFAULT_SEEDS = 3
BASE_SEED = 20260915


def seeds_for(count: int, base: int = BASE_SEED) -> list[int]:
    """The seed list a study uses, identical across arms so conditions stay paired."""
    return [base + i for i in range(count)]


@dataclass
class PlannedRun:
    arm: Arm
    seed: int
    run: GenerationRun


def plan(arm_labels: list[str], seeds: int) -> list[tuple[Arm, int]]:
    unknown = [label for label in arm_labels if label not in ARMS]
    if unknown:
        raise ValueError(f"unknown arm(s): {', '.join(unknown)}")
    return [(ARMS[label], seed) for label in arm_labels for seed in seeds_for(seeds)]


def launch(db: Session, blueprint: Blueprint, arm_labels: list[str], seeds: int = DEFAULT_SEEDS) -> list[PlannedRun]:
    """Create one run per (arm, seed). Execution is handed to the caller, which schedules them."""
    if blueprint.frozen_at is None:
        raise ValueError("a study may only be run against a frozen blueprint")
    planned = []
    for arm, seed in plan(arm_labels, seeds):
        run = orchestrator.start_run(db, blueprint, arm_label=arm.label, seed=seed)
        planned.append(PlannedRun(arm=arm, seed=seed, run=run))
    return planned


def study_runs(db: Session, blueprint_id: UUID) -> list[GenerationRun]:
    """Every arm-labelled run of a blueprint, oldest first."""
    return list(
        db.scalars(
            select(GenerationRun)
            .where(GenerationRun.blueprint_id == blueprint_id, GenerationRun.arm_label.is_not(None))
            .order_by(GenerationRun.started_at)
        ).all()
    )


def status(db: Session, blueprint_id: UUID) -> dict:
    """Where a study has got to, per arm."""
    runs = study_runs(db, blueprint_id)
    per_arm: dict[str, dict] = {}
    for run in runs:
        arm = get(run.arm_label)
        entry = per_arm.setdefault(
            run.arm_label,
            {"kind": arm.kind, "description": arm.description, "runs": 0, "completed": 0,
             "failed": 0, "running": 0, "candidates": 0, "seeds": []},
        )
        entry["runs"] += 1
        entry[run.status if run.status in ("completed", "failed", "running") else "running"] += 1
        entry["candidates"] += len(run.candidates)
        entry["seeds"].append(run.seed)
    return {
        "blueprint_id": str(blueprint_id),
        "total_runs": len(runs),
        "complete": all(r.status == "completed" for r in runs) if runs else False,
        "arms": per_arm,
    }
