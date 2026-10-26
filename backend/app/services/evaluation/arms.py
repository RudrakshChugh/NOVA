"""The baseline and ablation arms of plan §3.3.2 and §3.3.5, each pinned as a configuration.

An arm says which parts of the pipeline are *enforced*. Measurement is never switched off: every
arm's drafts are still checked for blueprint compliance and still audited for novelty, so a
constraint satisfaction rate and a duplicate rate exist for all of them. What an arm changes is
whether a failed check sends the draft back, and what the reviewer is shown.

The baselines share the model, the prompt templates and the corpus with the full system, so the
comparison isolates the auditing layers rather than confounding them with a change of model.
"""

from dataclasses import dataclass

FULL_SYSTEM = "full_system"
PLAIN_LLM = "plain_llm"
SOURCE_GROUNDED = "source_grounded"
FIXED_PROMPT_RAG = "fixed_prompt_rag"
ABLATION_NO_BLUEPRINT = "ablation_no_blueprint"
ABLATION_NO_NOVELTY = "ablation_no_novelty"
ABLATION_NO_RANKER = "ablation_no_ranker"


@dataclass(frozen=True)
class Arm:
    label: str
    description: str
    retrieval: bool  # ground the prompt in passages retrieved for the constraint
    full_material: bool  # instead, put the whole indexed material in the prompt
    blueprint_in_prompt: bool  # state the Bloom level, marks and type to the model
    enforce_compliance: bool  # send a non-compliant draft back to be repaired
    enforce_novelty: bool  # send a duplicate draft back to be rewritten
    rank: bool  # order candidates with the fitted ranker rather than the compliance floor
    kind: str  # baseline | ablation | system

    @property
    def grounded(self) -> bool:
        return self.retrieval or self.full_material


ARMS: dict[str, Arm] = {
    FULL_SYSTEM: Arm(
        label=FULL_SYSTEM,
        description="The complete pipeline: retrieval, blueprint enforcement, novelty auditing and the reviewer-adapted ranker.",
        retrieval=True, full_material=False, blueprint_in_prompt=True,
        enforce_compliance=True, enforce_novelty=True, rank=True, kind="system",
    ),
    PLAIN_LLM: Arm(
        label=PLAIN_LLM,
        description="Same model and prompt templates, no retrieval context, no blueprint stated, no checks enforced. The model is told only the topic.",
        retrieval=False, full_material=False, blueprint_in_prompt=False,
        enforce_compliance=False, enforce_novelty=False, rank=False, kind="baseline",
    ),
    SOURCE_GROUNDED: Arm(
        label=SOURCE_GROUNDED,
        description="The whole course material in context instead of retrieved passages, no blueprint stated, no checks enforced. Stands in for a source-grounded tool such as NotebookLM.",
        retrieval=False, full_material=True, blueprint_in_prompt=False,
        enforce_compliance=False, enforce_novelty=False, rank=False, kind="baseline",
    ),
    FIXED_PROMPT_RAG: Arm(
        label=FIXED_PROMPT_RAG,
        description="The same retrieval pipeline and a fixed prompt, but no blueprint enforcement, no novelty check and no ranker. The comparison the headline metric is reported against.",
        retrieval=True, full_material=False, blueprint_in_prompt=False,
        enforce_compliance=False, enforce_novelty=False, rank=False, kind="baseline",
    ),
    ABLATION_NO_BLUEPRINT: Arm(
        label=ABLATION_NO_BLUEPRINT,
        description="The full system with blueprint enforcement and the repair loop removed; drafts go straight from grounding to the novelty audit.",
        retrieval=True, full_material=False, blueprint_in_prompt=True,
        enforce_compliance=False, enforce_novelty=True, rank=True, kind="ablation",
    ),
    ABLATION_NO_NOVELTY: Arm(
        label=ABLATION_NO_NOVELTY,
        description="The full system with novelty enforcement removed; every grounded, compliant draft survives to ranking.",
        retrieval=True, full_material=False, blueprint_in_prompt=True,
        enforce_compliance=True, enforce_novelty=False, rank=True, kind="ablation",
    ),
    ABLATION_NO_RANKER: Arm(
        label=ABLATION_NO_RANKER,
        description="The full system with the fitted ranker replaced by the compliance-score-only floor; the same candidates, ordered differently.",
        retrieval=True, full_material=False, blueprint_in_prompt=True,
        enforce_compliance=True, enforce_novelty=True, rank=False, kind="ablation",
    ),
}

# The ranker ablation changes only the order of candidates, not which ones survive. Acceptance
# without edits is insensitive to order, so it is reported on NDCG@k instead (plan §3.3.5).
ORDER_ONLY_ARMS = frozenset({ABLATION_NO_RANKER})

HEADLINE_COMPARISON = (FULL_SYSTEM, FIXED_PROMPT_RAG)


def get(label: str | None) -> Arm:
    """The arm a run was executed under. A run with no label is normal platform use, which is the
    full system."""
    return ARMS[label] if label in ARMS else ARMS[FULL_SYSTEM]
