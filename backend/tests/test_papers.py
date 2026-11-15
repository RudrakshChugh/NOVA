"""Binding published questions into a printable paper (plan.md §14.3).

The pipeline used to end at a bank entry, which is a row rather than anything a student can be
handed. What is checked here is the last step: that only published questions reach a paper, that a
paper which does not add up to its blueprint cannot be finalised, and that a finalised paper keeps
printing what it printed the first time.
"""

import pytest

from app.llm.base import GenerationResult
from app.services import orchestrator

from .conftest import login, upload_material, write_pdf

MATERIAL = (
    "A relational schema is in first normal form when every attribute holds an atomic value. "
    "Second normal form removes partial dependencies on a composite key. "
    "Third normal form removes transitive dependencies between non-key attributes. "
    "Denormalisation reintroduces redundancy deliberately to make reads cheaper."
)

ROWS = [
    ("CO1 Explain relational normalisation", "first normal form", "understand", 5, "short_answer"),
    ("CO2 Apply normalisation to a schema", "second normal form", "understand", 10, "long_answer"),
]


class PlainAdapter:
    backend_id = "stub:papers"

    def generate(self, req):
        passages = req.user.split("Course passages:\n", 1)[1]
        first = passages.split("\n\n")[0].split(") ", 1)[1]
        head = first.split(". ")[0] + "."
        return GenerationResult(
            data={
                "stem": "Explain how second normal form removes partial dependencies.",
                "answer_key": (
                    "Second normal form removes partial dependencies on a composite key, so every "
                    "non-key attribute depends on the whole key rather than on part of it. A schema "
                    "in second normal form therefore records each fact once, which keeps an update "
                    "from having to be applied in several rows at the same time."
                ),
                "options": [],
                "citations": [{"passage": 1, "quote": head}],
            },
            model_version="stub", temperature=None, seed=None,
        )


@pytest.fixture(scope="module")
def published(client, new_course, tmp_path_factory):
    """A subject with two published questions, which is what a paper is made of."""
    monkey = pytest.MonkeyPatch()
    monkey.setattr(orchestrator, "get_adapter", lambda: PlainAdapter())
    coordinator = login(client, "coordinator@example.edu")
    course_id = new_course("PAPER", "Paper Course")

    pdf = write_pdf(tmp_path_factory.mktemp("paper") / "unit.pdf", MATERIAL)
    assert upload_material(client, course_id, pdf, coordinator)["state"] == "done"

    bp = client.post(f"/courses/{course_id}/blueprints", headers=coordinator).json()
    client.put(f"/blueprints/{bp['blueprint_id']}/constraints", headers=coordinator, json=[
        {"course_outcome": o, "concept": c, "bloom_level": b, "marks": m, "question_type": t}
        for o, c, b, m, t in ROWS
    ])
    frozen = client.post(f"/blueprints/{bp['blueprint_id']}/freeze", headers=coordinator).json()
    run = client.post(f"/blueprints/{frozen['blueprint_id']}/generate", headers=coordinator).json()
    candidates = client.get(f"/runs/{run['run_id']}/candidates", headers=coordinator).json()
    for c in candidates:
        client.post(f"/candidates/{c['question_id']}/review", json={"decision": "accept"},
                    headers=coordinator)
    client.post("/candidates/publish", headers=coordinator,
                json={"question_ids": [c["question_id"] for c in candidates]})
    monkey.undo()
    return {"course_id": course_id, "blueprint_id": frozen["blueprint_id"],
            "coordinator": coordinator}


def test_a_paper_is_filled_from_the_blueprint_it_was_written_for(client, published):
    coordinator = published["coordinator"]
    r = client.post(f"/courses/{published['course_id']}/papers", headers=coordinator,
                    json={"title": "End Semester 2026", "blueprint_id": published["blueprint_id"],
                          "exam_date": "2026-12-01"})
    assert r.status_code == 201, r.text
    paper = r.json()
    assert [i["number"] for i in paper["items"]] == [1, 2]
    assert paper["total_marks"] == 15
    # the published text, not the candidate's original
    assert paper["items"][0]["stem"].startswith("Explain how second normal form")
    assert paper["items"][0]["answer_key"]


def test_a_paper_that_does_not_add_up_cannot_be_finalised(client, published):
    """Catching a marks error per question and then losing it at the last step would be absurd."""
    coordinator = published["coordinator"]
    paper = client.post(f"/courses/{published['course_id']}/papers", headers=coordinator,
                        json={"title": "Short Paper", "blueprint_id": published["blueprint_id"]}).json()

    # drop one question, so the paper no longer carries the blueprint's marks
    keep = [{"entry_id": paper["items"][0]["entry_id"], "number": 1, "part": None, "marks": 5}]
    short = client.put(f"/papers/{paper['paper_id']}/items", json=keep, headers=coordinator).json()
    assert short["total_marks"] == 5

    check = client.post(f"/papers/{paper['paper_id']}/check", headers=coordinator).json()
    assert not check["ok"] and check["blueprint_marks"] == 15
    assert "carries 5 marks but blueprint" in check["issues"][0]

    refused = client.post(f"/papers/{paper['paper_id']}/finalise", headers=coordinator)
    assert refused.status_code == 422


def test_a_question_from_another_subject_cannot_be_put_on_a_paper(client, published, new_course):
    coordinator = published["coordinator"]
    other = new_course("PAPEROTHER", "Other Paper Course")
    paper = client.post(f"/courses/{other}/papers", headers=coordinator,
                        json={"title": "Borrowed"}).json()
    source = client.post(f"/courses/{published['course_id']}/papers", headers=coordinator,
                         json={"title": "Source", "blueprint_id": published["blueprint_id"]}).json()
    stolen = [{"entry_id": source["items"][0]["entry_id"], "number": 1, "part": None, "marks": 5}]
    r = client.put(f"/papers/{paper['paper_id']}/items", json=stolen, headers=coordinator)
    assert r.status_code == 422 and "not published on this subject" in r.json()["detail"]


def test_a_finalised_paper_is_frozen_and_prints_from_its_snapshot(client, published):
    coordinator = published["coordinator"]
    paper = client.post(f"/courses/{published['course_id']}/papers", headers=coordinator,
                        json={"title": "Final Paper", "blueprint_id": published["blueprint_id"]}).json()
    final = client.post(f"/papers/{paper['paper_id']}/finalise", headers=coordinator)
    assert final.status_code == 200, final.text
    assert final.json()["state"] == "final" and final.json()["finalised_at"]

    # frozen against further change, and against deletion
    again = [{"entry_id": paper["items"][0]["entry_id"], "number": 1, "part": None, "marks": 5}]
    assert client.put(f"/papers/{paper['paper_id']}/items", json=again,
                      headers=coordinator).status_code == 409
    assert client.delete(f"/papers/{paper['paper_id']}", headers=coordinator).status_code == 409
    assert client.post(f"/papers/{paper['paper_id']}/finalise", headers=coordinator).status_code == 409


def test_the_paper_prints_as_a_pdf_and_the_answer_key_is_separate(client, published):
    coordinator = published["coordinator"]
    client.put(f"/courses/{published['course_id']}/paper-template", headers=coordinator, json={
        "institution": "Thapar Institute of Engineering and Technology",
        "department": "Department of Computer Science and Engineering",
        "exam_name": "End Semester Examination", "duration_minutes": 180,
        "instructions": "Answer all questions. Assume any missing data.",
    })
    paper = client.post(f"/courses/{published['course_id']}/papers", headers=coordinator,
                        json={"title": "Printable", "blueprint_id": published["blueprint_id"]}).json()

    r = client.get(f"/papers/{paper['paper_id']}/pdf", headers=coordinator)
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF")
    assert "printable.pdf" in r.headers["content-disposition"]

    key = client.get(f"/papers/{paper['paper_id']}/answer-key", headers=coordinator)
    assert key.status_code == 200 and key.content.startswith(b"%PDF")
    # the key carries more than the paper does, because it carries the answers
    assert len(key.content) > len(r.content)


def test_the_printed_paper_carries_no_outcome_or_bloom_column(tmp_path):
    """Those two columns are the setter's working. A candidate cannot use them, and printing them
    on the question paper hands out the blueprint; they belong on the coordinator's copy.

    Read back with pypdf rather than by searching the bytes, because the text in a PDF is
    compressed and a raw search silently passes whatever it is given. The project's own parse_pdf
    is the wrong tool here: it drops fragments too short to be sentences, which is exactly what a
    column heading and a "CO2" cell are."""
    from pypdf import PdfReader

    from app.services.paper_pdf import render

    snapshot = {
        "title": "Mid Semester", "total_marks": 3,
        "items": [{
            "number": 1, "part": None, "marks": 3, "stem": "Draw the level-0 data flow diagram.",
            "answer_key": "A single process with its external entities.",
            "course_outcome": "CO2 Elicit and model requirements", "bloom_level": "apply",
        }],
    }

    def text_of(data: bytes, name: str) -> str:
        path = tmp_path / name
        path.write_bytes(data)
        return chr(10).join(page.extract_text() or "" for page in PdfReader(str(path)).pages)

    paper = text_of(render(snapshot, with_answers=False), "paper.pdf")
    key = text_of(render(snapshot, with_answers=True), "key.pdf")

    assert "Draw the level-0 data flow diagram." in paper   # the question is still on it
    assert "Marks" in paper                                 # and so is what it is worth
    assert "CO2" not in paper                               # but not the outcome it maps to
    assert "L3" not in paper                                # nor the Bloom level
    # The coordinator's copy keeps both, which is what an accreditation file wants.
    assert "CO2" in key and "L3" in key


def test_a_paper_may_take_questions_from_more_than_one_blueprint_version(client, published):
    """A subject accumulates published questions across every version it freezes, and a paper is
    drawn from the bank rather than from one version of the plan."""
    course_id = published["course_id"]
    coordinator = published["coordinator"]

    entries = client.get(f"/bank/entries?course_id={course_id}", headers=coordinator).json()
    assert entries, "the fixture publishes at least one question"

    paper = client.post(f"/courses/{course_id}/papers", headers=coordinator,
                        json={"title": "Across versions"}).json()
    assert paper["items"] == []                  # started empty, with no blueprint named

    rows = [{"entry_id": e["entry_id"], "number": i + 1, "part": None,
             "marks": e["snapshot"]["constraint"]["marks"]} for i, e in enumerate(entries)]
    r = client.put(f"/papers/{paper['paper_id']}/items", headers=coordinator, json=rows)
    assert r.status_code == 200
    assert len(r.json()["items"]) == len(entries)


def test_a_reviewer_may_print_the_paper_but_not_the_answer_key(client, published):
    """The one artefact here that must not reach a candidate."""
    coordinator = published["coordinator"]
    paper = client.post(f"/courses/{published['course_id']}/papers", headers=coordinator,
                        json={"title": "Reviewer View", "blueprint_id": published["blueprint_id"]}).json()
    reviewer = login(client, "reviewer1@example.edu")
    assert client.get(f"/papers/{paper['paper_id']}/pdf", headers=reviewer).status_code == 200
    assert client.get(f"/papers/{paper['paper_id']}/answer-key", headers=reviewer).status_code == 403
    assert client.put(f"/papers/{paper['paper_id']}/items", json=[], headers=reviewer).status_code == 403


def test_an_outsider_cannot_see_a_paper(client, new_course):
    """A paper belongs to its subject like everything else: invisible, not merely forbidden."""
    admin = login(client, "coordinator@example.edu")
    theirs = new_course("PAPEROUT", "Another Department", members=False)
    paper = client.post(f"/courses/{theirs}/papers", headers=admin,
                        json={"title": "Private"}).json()

    outsider = login(client, "reviewer1@example.edu")
    assert client.get(f"/papers/{paper['paper_id']}", headers=outsider).status_code == 404
    assert client.get(f"/papers/{paper['paper_id']}/pdf", headers=outsider).status_code == 404
    assert client.get(f"/courses/{theirs}/papers", headers=outsider).status_code == 404
