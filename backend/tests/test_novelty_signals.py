"""Unit tests for the parts of the novelty engine that are pure computation: the three
non-embedding signals, the combiner's protocol, pair construction and the split rule."""

import math

import pytest

from app.services import metrics
from app.services.novelty import combiner as cb
from app.services.novelty import pairs as pr
from app.services.novelty import signals as sg

CORPUS = {
    "a": "Explain how the congestion window of TCP changes after a packet loss is detected.",
    "b": "Draw a context diagram for a library management system and label every external entity.",
    "c": "Calculate the throughput of a link with a bandwidth of 10 Mbps and a delay of 20 ms.",
    "d": "List the four symbols used in a data flow diagram and state what each one depicts.",
}


@pytest.fixture(scope="module")
def index() -> sg.BM25Index:
    return sg.BM25Index(CORPUS)


# --- lexical -------------------------------------------------------------------------------


def test_lexical_is_one_against_itself(index):
    for text in CORPUS.values():
        assert index.lexical(text, text) == pytest.approx(1.0, abs=1e-9)


def test_lexical_ranks_the_matching_document_highest(index):
    query = "congestion window after packet loss in TCP"
    scores = {key: index.lexical(query, text) for key, text in CORPUS.items()}
    assert max(scores, key=scores.get) == "a"
    assert scores["a"] > scores["b"]


def test_lexical_is_bounded_and_zero_on_empty(index):
    assert index.lexical("", CORPUS["a"]) == 0.0
    assert index.lexical(CORPUS["a"], "") == 0.0
    assert 0.0 <= index.lexical(CORPUS["a"], CORPUS["b"]) <= 1.0


def test_lexical_scores_text_outside_the_corpus(index):
    """The corpus supplies IDF only, so a constructed variant can be scored against it."""
    variant = "Explain how TCP's congestion window changes once a lost packet is detected."
    assert index.lexical(CORPUS["a"], variant) > index.lexical(CORPUS["a"], CORPUS["b"])


# --- structural ----------------------------------------------------------------------------


def test_structural_ignores_clause_order():
    left = "Design a schema that is normalised, supports audit logging, and scales to 1M rows."
    right = "Design a schema that scales to 1M rows, supports audit logging, and is normalised."
    meta = sg.Metadata("long_answer", 10, "create")
    assert sg.structural(left, right, meta, meta) == pytest.approx(1.0)


def test_structural_penalises_different_metadata():
    text = "Explain the balancing rule for data flow diagrams."
    same = sg.structural(text, text, sg.Metadata("short_answer", 5, "understand"), sg.Metadata("short_answer", 5, "understand"))
    differs = sg.structural(text, text, sg.Metadata("short_answer", 5, "understand"), sg.Metadata("mcq", 1, "remember"))
    assert same > differs


def test_structural_skips_unknown_metadata():
    text = "Explain the balancing rule, state why it matters, and give one example."
    value = sg.structural(text, text, sg.Metadata(), sg.Metadata())
    assert value == pytest.approx(1.0)  # only the clause set is comparable, and it matches


# --- numerical -----------------------------------------------------------------------------


def test_numerical_is_high_when_only_numbers_differ():
    left = "Calculate the throughput of a link with a bandwidth of 10 Mbps and a delay of 20 ms."
    right = "Calculate the throughput of a link with a bandwidth of 45 Mbps and a delay of 70 ms."
    assert sg.numerical(left, right) == pytest.approx(1.0)


def test_numerical_is_low_for_a_different_question():
    assert sg.numerical(CORPUS["c"], CORPUS["b"]) < 0.5


def test_numerical_separates_renumbering_from_semantics():
    """The point of the signal: masking numbers lifts a renumbered pair above a topical pair."""
    renumbered = "Calculate the throughput of a link with a bandwidth of 99 Mbps and a delay of 1 ms."
    assert sg.numerical(CORPUS["c"], renumbered) > sg.numerical(CORPUS["c"], CORPUS["a"])


# --- semantic ------------------------------------------------------------------------------


def test_semantic_is_clipped_cosine():
    assert sg.semantic([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert sg.semantic([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert sg.semantic([1.0, 0.0], [-1.0, 0.0]) == 0.0  # clipped, never negative


# --- split rule ----------------------------------------------------------------------------


def test_pair_key_is_order_independent():
    a, b = "Explain X.", "Describe Y."
    assert pr.pair_key(a, b) == pr.pair_key(b, a)


def test_split_is_deterministic_and_independent_of_other_pairs():
    keys = [pr.pair_key(f"question {i}", f"variant {i}") for i in range(500)]
    first = {k: pr.assign_split(k) for k in keys}
    second = {k: pr.assign_split(k) for k in keys[:100]}  # a smaller set must not move anything
    assert all(first[k] == second[k] for k in second)


def test_split_proportions_are_close_to_the_committed_fractions():
    keys = [pr.pair_key(f"q{i}", f"v{i}") for i in range(4000)]
    counts = {"train": 0, "val": 0, "test": 0}
    for k in keys:
        counts[pr.assign_split(k)] += 1
    assert counts["train"] / len(keys) == pytest.approx(0.60, abs=0.03)
    assert counts["val"] / len(keys) == pytest.approx(0.20, abs=0.03)
    assert counts["test"] / len(keys) == pytest.approx(0.20, abs=0.03)


# --- transforms ----------------------------------------------------------------------------


def test_transforms_change_the_text_or_decline():
    import random

    rng = random.Random(0)
    source = "Calculate the delay of a 10 km link, state the assumption, and show the working."
    for name, fn in {**pr.HARD_TRANSFORMS, **pr.SURFACE_TRANSFORMS}.items():
        result = fn(source, rng)
        assert result is None or result.strip() != source.strip(), name


def test_renumber_preserves_everything_but_the_numbers():
    import random

    source = "Calculate the throughput for 10 Mbps over 20 ms."
    renumbered = pr.t_renumber(source, random.Random(1))
    assert renumbered is not None
    assert sg.mask_numbers(renumbered) == sg.mask_numbers(source)
    assert sg.numbers_in(renumbered) != sg.numbers_in(source)


def test_build_positives_and_negatives_are_labelled_correctly():
    import random

    questions = [(i, text) for i, text in enumerate(CORPUS.values())]
    rng = random.Random(7)
    positives = pr.build_positives(questions, rng, per_question=2)
    neighbours = {i: [(j, 0.9)] for i in range(len(questions)) for j in [(i + 1) % len(questions)]}
    negatives = pr.build_negatives(questions, neighbours, rng, 1, 1)
    assert positives and all(p.label for p in positives)
    assert negatives and not any(n.label for n in negatives)
    assert {p.stratum for p in positives} <= {pr.SURFACE, pr.HARD}


# --- combiner ------------------------------------------------------------------------------


def _separable_samples(n: int = 120) -> list[cb.Sample]:
    """Duplicates score high on every signal, non-duplicates low, with a little overlap."""
    out = []
    for i in range(n):
        duplicate = i % 2 == 0
        base = 0.80 if duplicate else 0.25
        jitter = ((i * 37) % 11) / 100.0
        out.append(({name: base + jitter for name in sg.SIGNAL_NAMES}, duplicate))
    return out


def test_fit_then_evaluate_reports_interpretable_weights_and_intervals():
    samples = _separable_samples()
    train, validation, test = samples[:72], samples[72:96], samples[96:]
    model = cb.fit(train, validation, version=1)

    assert model.is_fitted
    assert set(model.coefficients) == set(sg.SIGNAL_NAMES)
    assert 0.0 <= model.threshold <= 1.0

    result = cb.evaluate(model, test)
    assert result["n_test"] == len(test)
    for key in ("precision", "recall", "f1"):
        interval = result[key]
        assert interval["low"] <= interval["point"] <= interval["high"]
    assert result["f1"]["point"] > 0.8


def test_untrained_combiner_is_labelled_and_never_claims_to_be_fitted():
    model = cb.untrained() if hasattr(cb, "untrained") else cb.NoveltyCombiner.untrained()
    assert not model.is_fitted
    assert "untrained" in model.version
    high = model.probability({name: 1.0 for name in sg.SIGNAL_NAMES})
    low = model.probability({name: 0.0 for name in sg.SIGNAL_NAMES})
    assert high == pytest.approx(1.0) and low == pytest.approx(0.0)


def test_saved_combiner_round_trips(tmp_path):
    model = cb.fit(_separable_samples()[:80], _separable_samples()[80:], version=3)
    path = tmp_path / "novelty.json"
    model.save(path)
    loaded = cb.NoveltyCombiner.load(path)
    assert loaded.coefficients == model.coefficients
    assert loaded.threshold == model.threshold
    signals = {name: 0.7 for name in sg.SIGNAL_NAMES}
    assert loaded.probability(signals) == pytest.approx(model.probability(signals))


# --- metrics -------------------------------------------------------------------------------


def test_wilson_interval_stays_inside_zero_one():
    for successes, n in ((0, 10), (10, 10), (1, 3), (47, 50)):
        interval = metrics.wilson_interval(successes, n)
        assert 0.0 <= interval.low <= interval.point <= interval.high <= 1.0


def test_bootstrap_ci_brackets_the_point_estimate():
    data = [(True, True)] * 30 + [(True, False)] * 10 + [(False, False)] * 40 + [(False, True)] * 5
    interval = metrics.bootstrap_ci(data, metrics.f1, resamples=400)
    assert interval.low <= interval.point <= interval.high
    assert interval.point == pytest.approx(metrics.f1(data))


def test_mcnemar_reports_effect_size_with_its_p_value():
    paired = [(True, False)] * 20 + [(False, True)] * 5 + [(True, True)] * 30
    result = metrics.mcnemar(paired, seed=1)
    assert result.b == 20 and result.c == 5
    assert 0.0 < result.p_value < 0.01
    assert result.difference.low > 0  # the improvement's interval excludes zero


def test_mcnemar_on_a_tie_is_not_significant():
    paired = [(True, False)] * 10 + [(False, True)] * 10
    result = metrics.mcnemar(paired, seed=1)
    assert result.p_value > 0.5
    assert result.difference.low < 0 < result.difference.high


def test_cohens_kappa_matches_known_values():
    perfect = [("accept", "accept")] * 10 + [("reject", "reject")] * 10
    assert metrics.cohens_kappa(perfect) == pytest.approx(1.0)
    chance = [("accept", "accept"), ("accept", "reject"), ("reject", "accept"), ("reject", "reject")]
    assert metrics.cohens_kappa(chance) == pytest.approx(0.0, abs=1e-9)


def test_ndcg_rewards_putting_relevant_items_first():
    best = metrics.ndcg_at_k([1, 1, 0, 0], k=4)
    worst = metrics.ndcg_at_k([0, 0, 1, 1], k=4)
    assert best == pytest.approx(1.0)
    assert worst < best
    assert metrics.ndcg_at_k([0, 0, 0], k=3) is None  # undefined with no relevant item


def test_exact_binomial_is_symmetric():
    assert metrics._exact_binomial_two_sided(3, 10) == pytest.approx(
        metrics._exact_binomial_two_sided(7, 10)
    )
    assert metrics._exact_binomial_two_sided(0, 0) == 1.0
    assert not math.isnan(metrics._exact_binomial_two_sided(5, 10))


# --- institutional grid papers ------------------------------------------------------------------
# Built from the real layout of a Thapar MST paper: a Q No. / Questions / Marks / CO / BL table,
# flattened by PDF extraction so the cells land on their own lines, interleaved with the question
# text. The OCR damage is reproduced deliberately — "C05" for CO5 and "L I" for L1 are what the
# column actually produces, and a splitter that only handles clean text is no use on a real paper.

GRID_PAPER = """Roll Number:
Thapar Institute of Engineering and Technology, Patiala
Mid Semester Examination (MST)
Course Code: UCS503
Maximum Marks: 30
Note:
L Attempt All Questions
2. Assume suitable missing data (f any)
Q No. Questions Marks CO BL
Q1 You are part of a team building a social media application. The feature
should allow users to: 1. Create surveys in group chats; 2. Set a closing
date for the poll; 3. View outcomes in real time as members vote.
a) Which software process model would be most suitable?
b) Arrange these models in order of suitability.
05 CO1
C05
L2
Q2 Refer the case in Q1 and answer the following
a) Write four user stories in a suitable template.
02
b) Design a User Story Card for one of these user stories.
03
C05 L4
Q3 Consider the following Case: EduCare University plans to launch a Smart
05 CO2 L I
Learning Management System to support its online programmes.
a. Identify at least five functional requirements.
b. Identify at least five non-functional requirements
"""


def _grid(text=GRID_PAPER):
    from app.services import pyq_ingestion

    return pyq_ingestion.split_questions(text)


def test_grid_paper_splits_on_questions_not_on_the_lists_inside_them():
    questions = _grid()
    assert [q.number for q in questions] == ["1", "2", "3"]
    # "1. Create surveys; 2. Set a closing date" is a list inside Q1, not three questions
    assert "Create surveys in group chats" in questions[0].text
    assert "View outcomes in real time" in questions[0].text


def test_grid_paper_keeps_sub_parts_with_the_question_that_introduces_them():
    first, second, third = _grid()
    assert "Which software process model" in first.text and "order of suitability" in first.text
    assert "four user stories" in second.text and "User Story Card" in second.text
    assert "functional requirements" in third.text


def test_grid_paper_reads_marks_out_of_the_column_and_sums_sub_parts():
    first, second, third = _grid()
    assert first.marks == 5
    assert second.marks == 5, "02 and 03 are the two sub-parts of one 5-mark question"
    assert third.marks == 5
    assert sum(q.marks for q in _grid()) == 15  # the three questions this sample carries


def test_grid_paper_takes_bloom_from_the_level_the_paper_assigns():
    first, second, third = _grid()
    assert first.bloom_level == "understand"      # L2
    assert second.bloom_level == "analyze"        # L4
    assert third.bloom_level == "remember"        # "L I", which is OCR for L1


def test_grid_paper_drops_the_header_row_and_the_front_matter():
    joined = " ".join(q.text for q in _grid())
    for furniture in ("Roll Number", "Thapar Institute", "Q No.", "Attempt All Questions", "Maximum Marks"):
        assert furniture not in joined, furniture


def test_outcome_codes_are_not_mistaken_for_marks():
    """C05 is course outcome five, not five marks. Its digits must not reach the total."""
    from app.services.pyq_ingestion import _cell_marks

    assert _cell_marks("C05") == 0
    assert _cell_marks("CO2 L4") == 0
    assert _cell_marks("05 CO1") == 5
    assert _cell_marks("02") == 2


def test_a_paper_without_the_grid_still_uses_general_numbering():
    """The grid path must decline politely, not capture every paper it is offered."""
    from app.services import pyq_ingestion

    plain = "1. Define a data store with an example. [2]\n\n2. Explain balancing in a DFD. [5]\n"
    assert pyq_ingestion.split_grid_questions(plain) is None
    questions = pyq_ingestion.split_questions(plain)
    assert [q.marks for q in questions] == [2, 5]


def test_the_year_printed_on_a_paper_is_detected():
    """The year is the fact an uploader is most likely to get wrong, and a corpus that looks like
    one year undercuts the point of comparing across years. Detection is advisory: it is reported
    so a disagreement is visible, never used to overrule what was entered."""
    from app.services.pyq_ingestion import detect_year

    assert detect_year("Mid Semester Examination\nMST- Sept 26, 2024\nCourse Code: UCS503") == 2024
    assert detect_year("Course Code: UCS503\nDate: 27/9/2025 (3P.M- 5RM)") == 2025
    assert detect_year("Course Code: UCS503\nMaximum Marks: 30") is None
    # a course code is not a year, and neither is a mark total
    assert detect_year("UCS503 Maximum Marks: 1030") is None
