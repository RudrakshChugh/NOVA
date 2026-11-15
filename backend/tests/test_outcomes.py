"""Course outcomes read off a syllabus (plan.md §14.6).

Two things are being checked here. That the extractor reads the layouts real syllabi use, and that
nothing it reads is stored until a person has looked at it — because what it produces becomes the
vocabulary every blueprint on that subject is written against.
"""

import pytest

from app.services.syllabus import extract_outcomes

from .conftest import login, write_pdf

LABELLED = """
UCS503 Computer Networks
Course Outcomes
CO1: Explain the layered architecture of computer networks and the role of each layer.
CO2 Apply addressing and subnetting to design a small network.
CO3 - Analyse the behaviour of transport-layer protocols under congestion.
CO4. Evaluate routing algorithms against stated criteria such as convergence and overhead.
Syllabus
Unit 1: Introduction to networks, reference models, physical layer.
"""

LISTED = """
UCS503 Computer Networks
Course Outcomes
1. Explain the layered architecture of computer networks.
2. Apply addressing and subnetting to design a small network.
3. Analyse the behaviour of transport-layer protocols
   under congestion and loss.
Text Books
1. Tanenbaum, Computer Networks.
"""


# A real UCS503 syllabus, after the PDF parser has had it. The numbering a reader sees ("1.
# Analyze ...") is a list marker, and the parser strips list markers, so by the time the extractor
# runs there is nothing marking these lines as outcomes except the heading above them. Reported
# from the field: this shape used to extract nothing at all.
UNMARKED = """
Course Learning Outcomes (CLOs) / Course Objectives (COs):
On completion of this course, the students will be able to
Analyze software development process models for software development life cycle.
(MST)
Elicit, describe, and evaluate a system's requirements and analyze them using various
UML models.
(MST)
Demonstrate the use of design principles in designing data, architecture, user, and component-level design.
(EST)
Text Books:
Pressman R.S., Software Engineering: A Practitioner's Approach.
"""


def test_an_unmarked_list_under_a_heading_is_read():
    found = extract_outcomes(UNMARKED)
    assert [o.code for o in found] == ["CO1", "CO2", "CO3"]
    # Position is the only evidence, so every row is flagged for checking.
    assert all(o.confidence == 0.5 for o in found)
    assert found[0].text.startswith("Analyze software development process models")
    # the sentence that introduces the list is not one of them
    assert not any("will be able to" in o.text for o in found)
    # a wrapped tail belongs to the outcome above it rather than becoming its own row
    assert found[1].text.endswith("various UML models")
    # the assessment marker says when it is examined, not what it is
    assert not any("MST" in o.text or "EST" in o.text for o in found)
    # the list ends where the next section begins
    assert not any("Pressman" in o.text for o in found)


def test_a_labelled_syllabus_still_wins_over_the_unmarked_reading():
    """A document that labels its outcomes has already said which lines they are."""
    found = extract_outcomes(LABELLED)
    assert all(o.confidence == 1.0 for o in found)


def test_a_labelled_syllabus_is_read_at_full_confidence():
    found = extract_outcomes(LABELLED)
    assert [o.code for o in found] == ["CO1", "CO2", "CO3", "CO4"]
    assert all(o.confidence == 1.0 for o in found)
    assert found[0].text.startswith("Explain the layered architecture")
    # the label is stripped, and so is the punctuation that followed it
    assert not found[2].text.startswith("-")


def test_an_unlabelled_list_is_read_but_flagged():
    """The heading is the only evidence those lines are outcomes, so the extractor says so rather
    than presenting a guess as a reading."""
    found = extract_outcomes(LISTED)
    assert [o.code for o in found] == ["CO1", "CO2", "CO3"]
    assert all(o.confidence == 0.5 for o in found)
    # the list ends at the next section, so the textbook does not become an outcome
    assert not any("Tanenbaum" in o.text for o in found)


def test_a_wrapped_outcome_is_rejoined():
    found = extract_outcomes(LISTED)
    assert found[2].text.endswith("under congestion and loss")


def test_the_mapping_table_further_down_does_not_duplicate_an_outcome():
    """A syllabus states its outcomes and then repeats them, abbreviated, in a CO-PO mapping
    table. One row per code, keeping the first and fuller one."""
    text = LABELLED + "\nCO-PO mapping\nCO1: Explain layers\nCO2: Apply subnetting\n"
    found = extract_outcomes(text)
    assert [o.code for o in found] == ["CO1", "CO2", "CO3", "CO4"]
    assert found[0].text.endswith("role of each layer")


def test_a_document_with_no_outcomes_yields_none():
    assert extract_outcomes("Unit 1: Introduction. Unit 2: The physical layer.") == []


# --- the round trip through the API -----------------------------------------


@pytest.fixture
def subject(client, new_course):
    return new_course("OUTCOMES", "Outcome Course")


def test_a_syllabus_is_read_back_for_checking_and_not_stored(client, subject, tmp_path):
    coordinator = login(client, "coordinator@example.edu")
    pdf = write_pdf(tmp_path / "syllabus.pdf", LABELLED)
    with pdf.open("rb") as f:
        r = client.post(f"/courses/{subject}/syllabus",
                        files={"file": ("syllabus.pdf", f, "application/pdf")}, headers=coordinator)
    assert r.status_code == 200, r.text
    read = r.json()
    assert [o["code"] for o in read["outcomes"]] == ["CO1", "CO2", "CO3", "CO4"]
    assert "Nothing is saved until you save it." in read["note"]
    # reading is not saving
    assert client.get(f"/courses/{subject}/outcomes", headers=coordinator).json() == []


def test_outcomes_are_saved_then_stay_editable(client, subject):
    coordinator = login(client, "coordinator@example.edu")
    rows = [{"code": "CO1", "text": "Explain the layered architecture", "confidence": 1.0},
            {"code": "CO2", "text": "Apply addressing and subnetting", "confidence": 1.0}]
    saved = client.put(f"/courses/{subject}/outcomes", json=rows, headers=coordinator).json()
    assert [o["code"] for o in saved] == ["CO1", "CO2"]
    first_id = saved[0]["outcome_id"]

    # a reworded outcome keeps its identity, so anything pointing at it keeps pointing at it
    rows[0]["text"] = "Explain the layered architecture and the role of each layer"
    again = client.put(f"/courses/{subject}/outcomes", json=rows, headers=coordinator).json()
    assert again[0]["outcome_id"] == first_id
    assert again[0]["text"].endswith("role of each layer")


def test_two_outcomes_cannot_share_a_code(client, subject):
    coordinator = login(client, "coordinator@example.edu")
    rows = [{"code": "CO1", "text": "One thing"}, {"code": "CO1", "text": "Another thing"}]
    assert client.put(f"/courses/{subject}/outcomes", json=rows, headers=coordinator).status_code == 422


def test_a_reviewer_may_read_outcomes_but_not_write_them(client, subject):
    coordinator = login(client, "coordinator@example.edu")
    client.put(f"/courses/{subject}/outcomes", headers=coordinator,
               json=[{"code": "CO1", "text": "Explain the layered architecture"}])
    reviewer = login(client, "reviewer1@example.edu")
    assert client.get(f"/courses/{subject}/outcomes", headers=reviewer).status_code == 200
    assert client.put(f"/courses/{subject}/outcomes", json=[], headers=reviewer).status_code == 403


def test_a_blueprint_row_takes_its_wording_from_the_outcome_it_names(client, subject):
    coordinator = login(client, "coordinator@example.edu")
    outcomes = client.put(f"/courses/{subject}/outcomes", headers=coordinator, json=[
        {"code": "CO1", "text": "Explain the layered architecture"},
        {"code": "CO2", "text": "Apply addressing and subnetting"},
    ]).json()

    bp = client.post(f"/courses/{subject}/blueprints", headers=coordinator).json()
    rows = [{"course_outcome": "whatever the client sent", "outcome_id": outcomes[0]["outcome_id"],
             "concept": "reference models", "bloom_level": "understand", "marks": 5,
             "question_type": "short_answer"}]
    saved = client.put(f"/blueprints/{bp['blueprint_id']}/constraints", json=rows,
                       headers=coordinator).json()
    assert saved["constraints"][0]["course_outcome"] == "CO1 Explain the layered architecture"

    v = client.post(f"/blueprints/{bp['blueprint_id']}/validate", headers=coordinator).json()
    assert v["ok"] and v["uncovered"] == ["CO2 Apply addressing and subnetting"]


def test_a_row_that_names_no_outcome_is_an_issue_once_the_subject_has_them(client, subject):
    """Free text was all there was before outcomes existed. Once a subject states them, a row that
    ignores them cannot be counted toward coverage, so validation says so instead of silently
    reporting a gap that is not real."""
    coordinator = login(client, "coordinator@example.edu")
    client.put(f"/courses/{subject}/outcomes", headers=coordinator,
               json=[{"code": "CO1", "text": "Explain the layered architecture"}])
    bp = client.post(f"/courses/{subject}/blueprints", headers=coordinator).json()
    client.put(f"/blueprints/{bp['blueprint_id']}/constraints", headers=coordinator,
               json=[{"course_outcome": "CO1 typed by hand", "concept": "reference models",
                      "bloom_level": "understand", "marks": 5, "question_type": "short_answer"}])
    v = client.post(f"/blueprints/{bp['blueprint_id']}/validate", headers=coordinator).json()
    assert not v["ok"] and "not linked" in v["issues"][0]


def test_a_blueprint_naming_an_outcome_of_another_subject_is_refused(client, subject, new_course):
    coordinator = login(client, "coordinator@example.edu")
    other = new_course("OTHEROUT", "Other Outcomes")
    theirs = client.put(f"/courses/{other}/outcomes", headers=coordinator,
                        json=[{"code": "CO1", "text": "Something else entirely"}]).json()
    bp = client.post(f"/courses/{subject}/blueprints", headers=coordinator).json()
    r = client.put(f"/blueprints/{bp['blueprint_id']}/constraints", headers=coordinator,
                   json=[{"course_outcome": "x", "outcome_id": theirs[0]["outcome_id"],
                          "concept": "c", "bloom_level": "understand", "marks": 5,
                          "question_type": "short_answer"}])
    assert r.status_code == 422


def test_removing_an_outcome_unlinks_the_rows_rather_than_breaking_them(client, subject):
    coordinator = login(client, "coordinator@example.edu")
    outcomes = client.put(f"/courses/{subject}/outcomes", headers=coordinator, json=[
        {"code": "CO1", "text": "Explain the layered architecture"},
        {"code": "CO2", "text": "Apply addressing and subnetting"},
    ]).json()
    bp = client.post(f"/courses/{subject}/blueprints", headers=coordinator).json()
    client.put(f"/blueprints/{bp['blueprint_id']}/constraints", headers=coordinator,
               json=[{"course_outcome": "x", "outcome_id": outcomes[1]["outcome_id"],
                      "concept": "subnetting", "bloom_level": "apply", "marks": 5,
                      "question_type": "numerical"}])

    client.put(f"/courses/{subject}/outcomes", headers=coordinator,
               json=[{"code": "CO1", "text": "Explain the layered architecture"}])
    after = client.get(f"/blueprints/{bp['blueprint_id']}", headers=coordinator).json()
    row = after["constraints"][0]
    assert row["outcome_id"] is None
    # the wording it was written with survives, so the question it produced still means something
    assert row["course_outcome"] == "CO2 Apply addressing and subnetting"
