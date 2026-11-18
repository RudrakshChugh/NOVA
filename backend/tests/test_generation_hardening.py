"""The parts of generation that must hold whatever the model sends back, and the rules that make
a draft outcome-aligned and new. None of these needs a database: they are the pure functions the
orchestrator is built from, so they run anywhere the package imports."""

from types import SimpleNamespace

import pytest

from app.llm.base import MalformedOutput, parse_json_object
from app.routers.blueprints import level_warnings
from app.services import compliance, orchestrator
from app.services.evaluation import arms as A
from app.services.novelty import Corpus, Reference, score_against_references, similar_past_questions
from app.services.novelty.combiner import NoveltyCombiner
from app.services.novelty.signals import BM25Index, Metadata


def _row(**over):
    base = dict(
        course_outcome="CO2 Analyze software requirements using structured techniques",
        concept="data flow diagrams", bloom_level="analyze", marks=5,
        question_type="short_answer", position=0, outcome_id=None,
    )
    return SimpleNamespace(**{**base, **over})


def _passage():
    return SimpleNamespace(page_ref="p3", text="A data flow diagram shows processes. It has four symbols.")


# --- a reply that is not a question ----------------------------------------------------------


def test_a_fenced_json_reply_is_read_rather_than_rejected():
    assert parse_json_object('```json\n{"stem": "s"}\n```', "X") == {"stem": "s"}


@pytest.mark.parametrize("reply", ["", "   ", "Sure! Here is your question", "[1, 2]", "null"])
def test_a_reply_that_is_not_a_json_object_is_malformed(reply):
    with pytest.raises(MalformedOutput):
        parse_json_object(reply, "X")


def test_malformed_output_is_still_a_runtime_error():
    """Callers that catch RuntimeError for "the backend failed" must keep catching this."""
    assert issubclass(MalformedOutput, RuntimeError)


@pytest.mark.parametrize("data", [
    None, "a string", [], {}, {"stem": "", "answer_key": "a"}, {"stem": "s"},
    {"stem": "s", "answer_key": None}, {"stem": 12, "answer_key": "a"},
])
def test_a_reply_with_no_question_in_it_is_malformed(data):
    with pytest.raises(MalformedOutput):
        orchestrator.normalise_draft(data)


def test_everything_short_of_a_missing_question_is_repaired():
    out = orchestrator.normalise_draft({
        "stem": "  Compare X and Y.  ", "answer_key": 42, "options": None,
        "citations": [
            {"passage": "2", "quote": " exact words "},   # number sent as a string
            {"passage": "two", "quote": "x"},             # unreadable number: dropped
            {"passage": 1},                               # no quote: dropped
            "not an object",
            {"passage": 3, "quote": "   "},
        ],
    })
    assert out == {
        "stem": "Compare X and Y.", "answer_key": "42", "options": [],
        "citations": [{"passage": 2, "quote": "exact words"}],
    }


def test_blank_options_are_dropped_and_the_rest_kept():
    out = orchestrator.normalise_draft({"stem": "s", "answer_key": "a", "options": ["A", " ", "B"], "citations": []})
    assert out["options"] == ["A", "B"]


def test_a_malformed_reply_is_retried_without_failing(monkeypatch):
    monkeypatch.setattr(orchestrator.settings, "malformed_retry_budget", 2)
    replies = [{"stem": ""}, "garbage", {"stem": "Compare X and Y.", "answer_key": "Because.", "options": [], "citations": []}]

    class Adapter:
        calls = 0

        def generate(self, req):
            Adapter.calls += 1
            return SimpleNamespace(data=replies.pop(0), temperature=0.7, seed=1)

    _, data = orchestrator._draft(Adapter(), None)
    assert data["stem"] == "Compare X and Y." and Adapter.calls == 3


def test_a_reply_that_stays_malformed_gives_up_with_the_reason(monkeypatch):
    monkeypatch.setattr(orchestrator.settings, "malformed_retry_budget", 1)

    class Adapter:
        def generate(self, req):
            return SimpleNamespace(data={"stem": ""}, temperature=None, seed=None)

    with pytest.raises(MalformedOutput, match="no usable `stem`.*after 2 attempts"):
        orchestrator._draft(Adapter(), None)


# --- seeds -----------------------------------------------------------------------------------


def test_rows_of_one_run_get_different_seeds_and_the_same_ones_every_time():
    seeds = [orchestrator.row_seed(1000, i) for i in range(4)]
    assert len(set(seeds)) == 4
    assert seeds == [orchestrator.row_seed(1000, i) for i in range(4)]
    assert orchestrator.row_seed(1000, 0) == 1000  # the first row keeps the run's own seed
    assert orchestrator.row_seed(None, 3) is None
    assert 0 <= orchestrator.row_seed(2**31 - 1, 5) < 2**31


# --- the prompt states the rules the audit applies ---------------------------------------------


def test_the_prompt_names_the_verbs_the_bloom_check_accepts():
    prompt = orchestrator._build_user_prompt(_row(), [_passage()], [])
    for verb in compliance.cue_verbs("analyze"):
        assert verb.capitalize() in prompt
    # and a stem opening with any of them really does classify at that level
    for verb in compliance.cue_verbs("analyze"):
        assert compliance.classify_bloom(f"{verb.capitalize()} the two notations.") == "analyze"


@pytest.mark.parametrize("level", list(compliance.BLOOM_CUES))
def test_every_verb_offered_for_a_level_passes_that_levels_check(level):
    for verb in compliance.cue_verbs(level):
        assert compliance.classify_bloom(f"{verb.capitalize()} the given system.") == level, verb


def test_the_prompt_states_the_answer_length_the_marks_check_enforces():
    low, high = compliance.mark_band(5)
    prompt = orchestrator._build_user_prompt(_row(marks=5), [_passage()], [])
    assert f"between {low} and {high} words" in prompt
    assert "at least 200 words" in orchestrator._build_user_prompt(_row(marks=15, question_type="long_answer"), [_passage()], [])


def test_an_mcq_is_given_no_length_rule_because_none_is_checked():
    prompt = orchestrator._build_user_prompt(_row(question_type="mcq", marks=1), [_passage()], [])
    assert "Answer-key length" not in prompt


def test_the_outcome_is_stated_as_what_the_marks_are_for():
    prompt = orchestrator._build_user_prompt(_row(), [_passage()], [])
    assert "Course outcome being assessed:\n  CO2 Analyze software requirements" in prompt


def test_originality_wording_follows_the_level():
    lower = orchestrator._build_user_prompt(_row(bloom_level="remember"), [_passage()], [])
    higher = orchestrator._build_user_prompt(_row(bloom_level="apply"), [_passage()], [])
    assert "new situation" not in lower and "in your own words" in lower
    assert "new situation" in higher


def test_past_questions_and_siblings_are_listed_for_the_model_to_avoid():
    prompt = orchestrator._build_user_prompt(
        _row(), [_passage()], [], avoid=["Draw a level-0 DFD for a library."],
        siblings=["Compare a context diagram with a level-1 DFD."],
    )
    assert "- Draw a level-0 DFD for a library." in prompt
    assert "- Compare a context diagram with a level-1 DFD." in prompt
    # the phrase that marks a *redraft* must not appear on a first draft
    assert "already been set in a past paper" not in prompt and "previous draft" not in prompt


def test_an_arm_that_does_not_enforce_novelty_is_not_shown_past_questions():
    arm = A.get(A.ABLATION_NO_NOVELTY)
    prompt = orchestrator._build_user_prompt(_row(), [_passage()], [], arm=arm, avoid=["Past question."], siblings=["Sibling."])
    assert "Past question." not in prompt and "Sibling." not in prompt


def test_a_baseline_prompt_is_unchanged_by_any_of_this():
    arm = A.get(A.FIXED_PROMPT_RAG)
    prompt = orchestrator._build_user_prompt(_row(), [_passage()], [], arm=arm, avoid=["Past question."])
    assert prompt.startswith("Topic:") and "Bloom" not in prompt and "Past question." not in prompt


def test_the_redraft_names_where_the_repeat_was_found():
    def redraft(source):
        return orchestrator._build_user_prompt(_row(), [_passage()], [], "Old question?", duplicate_source=source)

    assert "has already been set in a past paper" in redraft("pyq")
    assert "is already on this paper" in redraft("run")
    assert "is already in the question bank" in redraft("bank")


def test_the_prompt_keeps_the_layout_the_passages_are_read_from():
    prompt = orchestrator._build_user_prompt(_row(), [_passage()], [], avoid=["a"], siblings=["b"])
    assert prompt.startswith("Blueprint constraint:")
    body = prompt.split("Course passages:\n", 1)[1]
    assert body.split("\n\n")[0] == "[1] (p3) A data flow diagram shows processes. It has four symbols."


def test_a_long_listed_question_is_trimmed_not_dropped():
    prompt = orchestrator._build_user_prompt(_row(), [_passage()], [], avoid=["word " * 200])
    line = next(ln for ln in prompt.splitlines() if ln.startswith("- word"))
    assert len(line) <= orchestrator.MAX_LISTED_CHARS + 2 and line.endswith("…")


def test_the_prompt_hash_changes_with_the_suggestion_and_only_then():
    assert orchestrator.prompt_hash() == orchestrator.prompt_hash(None) == orchestrator.prompt_hash("")
    assert orchestrator.prompt_hash("numerical please") != orchestrator.prompt_hash()


# --- siblings --------------------------------------------------------------------------------


def test_only_rows_on_the_same_outcome_or_concept_count_as_siblings():
    row = _row()
    drafted = [
        (_row(concept="Data Flow Diagrams ", course_outcome="CO9 other"), "same concept"),
        (_row(concept="testing", course_outcome=row.course_outcome), "same outcome"),
        (_row(concept="testing", course_outcome="CO9 other"), "unrelated"),
    ]
    assert orchestrator._related_siblings(row, drafted) == ["same concept", "same outcome"]


# --- novelty beyond past papers ----------------------------------------------------------------


def _unit(*xs):
    norm = sum(x * x for x in xs) ** 0.5
    return [x / norm for x in xs]


def test_only_past_questions_near_the_row_are_offered_to_the_model():
    near = SimpleNamespace(text="near", embedding=_unit(1, 0.1, 0))
    far = SimpleNamespace(text="far", embedding=_unit(0, 0, 1))
    corpus = Corpus(questions=[far, near], index=BM25Index({}), combiner=NoveltyCombiner.untrained())
    assert [q.text for q in similar_past_questions(corpus, _unit(1, 0, 0), k=3, floor=0.35)] == ["near"]
    assert similar_past_questions(corpus, _unit(1, 0, 0), k=0, floor=0.0) == []
    assert similar_past_questions(None, _unit(1, 0, 0), k=3, floor=0.0) == []


def test_a_draft_identical_to_an_earlier_question_of_the_run_is_a_duplicate():
    """Same four signals, same combiner, same threshold as for a past paper."""
    corpus = Corpus(questions=[], index=BM25Index({}), combiner=NoveltyCombiner.untrained())
    meta = Metadata(question_type="short_answer", marks=5, bloom_level="analyze")
    stem = "Compare a context diagram with a level-1 data flow diagram for a library system."
    same = Reference(text=stem, embedding=_unit(1, 0, 0), meta=meta, source="run", label="Q1 of this run")
    other = Reference(
        text="Define cyclomatic complexity and state its formula.", embedding=_unit(0, 1, 0),
        meta=Metadata(question_type="short_answer", marks=2, bloom_level="remember"), source="bank", label="question bank",
    )
    scored = score_against_references(stem, _unit(1, 0, 0), meta, corpus, [other, same], shortlist=10)
    top, signals, probability = scored[0]
    assert top is same and probability >= corpus.combiner.threshold
    assert all(signals[name] == pytest.approx(1.0) for name in signals)
    assert scored[1][2] < corpus.combiner.threshold


def test_no_references_means_nothing_to_score():
    corpus = Corpus(questions=[], index=BM25Index({}), combiner=NoveltyCombiner.untrained())
    assert score_against_references("q", [1.0], Metadata(), corpus, [], shortlist=5) == []


# --- outcome level -----------------------------------------------------------------------------


@pytest.mark.parametrize("text, level", [
    ("CO1 Explain the phases of the software life cycle", "understand"),
    ("CO 3: Design a normalised schema for a given domain", "create"),
    ("C02 - Apply estimation techniques to a project", "apply"),
    ("Analyze requirements using structured techniques", "analyze"),
    ("CO4 Familiarity with testing", None),
])
def test_the_level_an_outcome_is_stated_at_is_read_from_its_verb(text, level):
    assert compliance.outcome_bloom_level(text) == level


def test_a_row_above_its_outcomes_level_is_warned_about_and_nothing_else_is():
    rows = [
        _row(course_outcome="CO1 Explain DFD notation", bloom_level="create", position=0),      # above
        _row(course_outcome="CO3 Design a schema", bloom_level="apply", position=1),            # below: fine
        _row(course_outcome="CO1 Explain DFD notation", bloom_level="understand", position=2),  # equal: fine
        _row(course_outcome="CO4 Familiarity with testing", bloom_level="create", position=3),  # unknown: silent
    ]
    warnings = level_warnings(rows)
    assert len(warnings) == 1
    assert "Row 1" in warnings[0] and "CO1" in warnings[0] and "understand" in warnings[0]


# --- past-paper layouts ------------------------------------------------------------------------
# Both taken from real departmental papers that used to come out as one undivided "question",
# which left the novelty engine comparing drafts against a page of text instead of questions.


def test_a_paper_numbered_with_bare_numbers_and_marks_on_their_own_line_is_split():
    from app.services.pyq_ingestion import split_questions

    text = "\n".join([
        "Thapar Institute of Engineering and Technology, Patiala",
        "Time:3 Hours;MM: 100 Name of Faculty: Dr. X",
        "Note: Attempt all Questions. Assume missing data, if any",
        "1 Describe the variations between the Agile and Waterfall development models.", "10",
        "2 Describe the steps involved in requirements analysis and collection.",
        "Why is it crucial to have precise requirements?", "10",
        "3 In software engineering, what are design patterns? Give three instances.", "10",
    ])
    questions = split_questions(text)
    assert [q.number for q in questions] == ["1", "2", "3"]
    assert [q.marks for q in questions] == [10, 10, 10]
    assert questions[1].text.endswith("Why is it crucial to have precise requirements?")
    assert "Thapar" not in questions[0].text


def test_a_sentence_that_happens_to_start_with_a_number_does_not_open_a_question():
    from app.services.pyq_ingestion import split_bare_numbered

    text = "\n".join([
        "1 Explain the spiral model and its four phases in detail.",
        "4 Phases are repeated in every loop of the model.",  # not question 2
        "2 Describe risk analysis in the spiral model briefly.",
        "3 Compare the spiral model with the waterfall model.",
    ])
    questions = split_bare_numbered(text)
    assert [q.number for q in questions] == ["1", "2", "3"]
    assert "4 Phases are repeated" in questions[0].text


def test_ordinary_numbering_is_never_reread_under_the_bare_number_rule():
    from app.services.pyq_ingestion import split_questions

    text = "Q1. Explain second normal form with an example.\n\nQ2. Define a transitive dependency and give one instance."
    assert [q.number for q in split_questions(text)] == ["1", "2"]


def test_a_grid_paper_whose_question_markers_sit_alone_on_their_line_is_split():
    from app.services.pyq_ingestion import split_questions

    text = "\n".join([
        "Ques Questions Marks CO BL",
        "Q1 What is the difference between composition and inheritance in design?", "10 CO1 L2",
        "Q2a",
        "2b.",
        "Draw the state chart diagram for the given vending machine scenario.", "10 CO3",
        "Q3a",
        "Q3b",
        "A project is estimated to have 20000 lines of code. Compute the effort.", "10 CO2 L3",
    ])
    questions = split_questions(text)
    assert [q.number for q in questions] == ["1", "2", "3"]
    assert [q.marks for q in questions] == [10, 10, 10]
    assert questions[1].text.startswith("Draw the state chart") and "2b" not in questions[1].text
    assert questions[0].bloom_level == "understand" and questions[2].bloom_level == "apply"


def test_citation_markers_that_leak_into_the_text_are_removed_and_nothing_else_is():
    out = orchestrator.normalise_draft({
        "stem": "Compare the two diagrams (see passage 2).",
        "answer_key": "First, identify the entities (cite [1]). The matrix A[1] holds the weights [cite: 3].",
        "options": [], "citations": [],
    })
    assert out["stem"] == "Compare the two diagrams."
    assert out["answer_key"] == "First, identify the entities. The matrix A[1] holds the weights."


def test_the_prompt_forbids_passage_numbers_in_the_printed_text():
    prompt = orchestrator._build_user_prompt(_row(), [_passage()], [])
    assert "no passage numbers" in prompt and "no first person" in prompt


def test_longer_leaked_references_and_bare_passage_numbers_are_removed_too():
    out = orchestrator.normalise_draft({
        "stem": "Compare the two diagrams.",
        "answer_key": "Data objects are transformed by functions (see passage 1 \u201cFlow-oriented modeling provides an "
                      "indication of how data objects are transformed\u201d). It establishes the capability of the system (passage 2).",
        "options": [], "citations": [],
    })
    assert out["answer_key"] == "Data objects are transformed by functions. It establishes the capability of the system."


def test_a_numbered_list_in_an_answer_is_not_mistaken_for_a_citation():
    """A bare "(1)" used to be stripped as a leaked passage number, which ate list numbering."""
    answer = "The steps are (1) identify the actors, (2) list the use cases. Use formula (3)."
    out = orchestrator.normalise_draft({"stem": "Compare X and Y.", "answer_key": answer, "options": [], "citations": []})
    assert out["answer_key"] == answer


def test_higher_levels_are_asked_for_the_result_not_a_description_of_the_method():
    higher = orchestrator._build_user_prompt(_row(bloom_level="analyze"), [_passage()], [])
    lower = orchestrator._build_user_prompt(_row(bloom_level="understand"), [_passage()], [])
    assert "ask for the result itself" in higher and "outline the steps" in higher
    assert "ask for the result itself" not in lower


def test_rows_that_ask_for_the_same_thing_twice_are_pointed_out():
    from app.routers.blueprints import twin_warnings

    rows = [
        _row(position=0), _row(position=1, marks=8),            # same outcome, concept, level
        _row(position=2, bloom_level="apply"),                  # different level: fine
        _row(position=3, concept="Data Flow Diagrams "),         # same again, differently typed
    ]
    warnings = twin_warnings(rows)
    assert len(warnings) == 2
    assert "Rows 1 and 2" in warnings[0] and "Rows 1 and 4" in warnings[1]
    assert twin_warnings([_row(position=0), _row(position=1, concept="use case diagram")]) == []


# --- Bloom: the opening verb is not the whole question -----------------------------------------


@pytest.mark.parametrize("stem", [
    "Analyze the scenario where a university automates registration. Describe how you would analyze the requirements, outlining the key steps you would take.",
    "Apply structured analysis to a library system and outline the steps you would take to draw its use case diagram.",
    "Analyze the given case and explain the process of deriving a level-1 diagram from it.",
    "Design a test plan for the login module. How would you go about choosing the test cases?",
])
def test_a_higher_level_verb_in_front_of_a_request_for_the_method_fails_the_bloom_check(stem):
    level = compliance.classify_bloom(stem)
    row = _row(bloom_level=level, marks=5, concept="analysis", course_outcome="CO2 Analyze the given case")
    report = compliance.check(row, stem, "analysis " * 40, None, 0.9)
    failed = [v for v in report.violations if v.field == "bloom_level"]
    assert len(failed) == 1 and "description of a procedure" in failed[0].observed


@pytest.mark.parametrize("stem", [
    "Compare a context diagram with a level-1 data flow diagram for a hostel mess billing system.",
    "Construct the level-0 data flow diagram for the given library system.",
    "Compute the function point count from the figures given below.",
    "Trace the steps a reservation request passes through in the diagram below.",   # steps as content, not as the ask
])
def test_asking_for_the_result_itself_passes(stem):
    level = compliance.classify_bloom(stem)
    row = _row(bloom_level=level, marks=5, concept="data flow diagram function point", course_outcome="CO2 Analyze data flow")
    report = compliance.check(row, stem, "data flow diagram function point " * 10, None, 0.9)
    assert not [v for v in report.violations if v.field == "bloom_level"]


def test_a_recall_question_may_ask_for_steps():
    """Below `apply`, the steps of a method are a legitimate thing to ask for."""
    stem = "List the steps of drawing a data flow diagram."
    report = compliance.check(_row(bloom_level="remember", marks=2), stem, "data flow diagram steps " * 5, None, 0.9)
    assert not [v for v in report.violations if v.field == "bloom_level"]


# --- twin rows are made different by construction ------------------------------------------------


def _c(i, **over):
    return _row(position=i, constraint_id=f"c{i}", **over)


def test_rows_that_share_outcome_concept_and_level_form_a_group_and_others_do_not():
    rows = [_c(0), _c(1), _c(2, concept="use case diagram"), _c(3), _c(4, bloom_level="apply")]
    assert orchestrator.twin_groups(rows) == {"c0": (0, 3), "c1": (1, 3), "c3": (2, 3)}
    assert orchestrator.twin_groups([_c(0), _c(1, concept="other")]) == {}


def test_each_twin_is_given_a_different_form_of_task_and_a_lone_row_none():
    first = orchestrator._build_user_prompt(_row(), [_passage()], [], twin=(0, 2))
    second = orchestrator._build_user_prompt(_row(), [_passage()], [], twin=(1, 2))
    alone = orchestrator._build_user_prompt(_row(), [_passage()], [])
    forms = orchestrator.TASK_FORMS["analyze"]
    assert f"Task form for this row: {forms[0]}." in first and f"Task form for this row: {forms[1]}." in second
    assert "Task form for this row" not in alone


def test_every_level_has_enough_distinct_task_forms():
    assert set(orchestrator.TASK_FORMS) == set(compliance.BLOOM_CUES)
    for forms in orchestrator.TASK_FORMS.values():
        assert len(forms) >= 4 and len(set(forms)) == len(forms)
        # a task form must not itself be a request to describe a procedure
        assert not any(compliance.procedure_ask(f) for f in forms)


# --- evidence check: only a quote that is really there counts ----------------------------------


def _passages():
    return [
        SimpleNamespace(page_ref="p12", text="A data flow diagram models how data moves through a system. It uses four symbols: process, data store, external entity and data flow."),
        SimpleNamespace(page_ref="p13", text="A context diagram shows the whole system as\na single process connected to its external entities."),
    ]


def test_a_statement_is_supported_only_when_its_quote_is_found_verbatim():
    from app.services import claims

    report = claims.verify({"claims": [
        {"text": "A DFD uses four symbols.", "kind": "material", "passage": 1, "quote": "It uses four symbols: process, data store, external entity and data flow."},
        {"text": "A context diagram shows one process.", "kind": "material", "passage": 1,   # wrong number, real quote, line break in source
         "quote": "A context diagram shows the whole system as a single process connected to its external entities."},
        {"text": "A DFD must have exactly seven processes.", "kind": "material", "passage": 1, "quote": "A data flow diagram must have exactly seven processes."},
        {"text": "It was standardised in 1994.", "kind": "material", "passage": 0, "quote": ""},
        {"text": "Short quote.", "kind": "material", "passage": 1, "quote": "four symbols"},   # too short to prove anything
        {"text": "The Patron is an external entity of the library system.", "kind": "scenario", "passage": 0, "quote": ""},
    ]}, _passages())
    assert [c["verified"] for c in report["claims"]] == [True, True, False, False, False, False]
    assert report["claims"][1]["page_ref"] == "p13"
    assert report["claims"][2]["quote"] is None, "an unverified quote is not shown as evidence"
    assert (report["material_total"], report["material_verified"], report["scenario_total"]) == (5, 2, 1)


@pytest.mark.parametrize("raw", [None, "text", {}, {"claims": "no"}, {"claims": []}, {"claims": [{"kind": "material"}]}])
def test_an_unusable_reply_gives_no_report_rather_than_a_wrong_one(raw):
    from app.services import claims

    assert claims.verify(raw, _passages()) is None


def test_the_evidence_check_never_raises_and_names_who_made_it():
    from app.services import claims

    class Down:
        backend_id = "stub:down"

        def generate(self, req):
            raise RuntimeError("unreachable")

    class Up:
        backend_id = "stub:up"

        def generate(self, req):
            assert "Answer key:" in req.user and "[1] " in req.user and req.temperature == 0.0
            return SimpleNamespace(data={"claims": [{"text": "A DFD uses four symbols.", "kind": "material", "passage": 1,
                                                     "quote": "It uses four symbols: process, data store, external entity and data flow."}]})

    assert claims.check(Down(), "q", "a", _passages()) is None
    assert claims.check(Up(), "q", "a", []) is None
    report = claims.check(Up(), "q", "a", _passages())
    assert report["material_verified"] == 1 and report["checked_by"] == "stub:up"
