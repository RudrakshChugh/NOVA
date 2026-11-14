"""Uploading several files at once (plan.md §14.2).

A coordinator holding a folder of lecture decks used to repeat the same interaction twelve times,
and each one blocked while its passages were embedded. The batch endpoint is only an improvement if
each file keeps its own outcome: an aggregate error on twelve files says nothing about which of the
twelve went wrong, and one corrupt PDF must not discard the other eleven.
"""

from .conftest import login, write_pdf

MATERIAL_A = (
    "A relational schema is in first normal form when every attribute holds an atomic value. "
    "Second normal form removes partial dependencies on a composite key. "
    "Third normal form removes transitive dependencies between non-key attributes."
)
MATERIAL_B = (
    "A transaction is atomic when it either completes entirely or has no effect at all. "
    "Isolation keeps concurrent transactions from observing each other's intermediate state. "
    "Durability means a committed transaction survives a crash of the database server."
)


def _jobs(client, course_id, headers):
    return client.get(f"/courses/{course_id}/ingestion-jobs", headers=headers).json()


def test_a_batch_is_indexed_file_by_file(client, new_course, tmp_path):
    coordinator = login(client, "coordinator@example.edu")
    course_id = new_course("BATCH", "Batch Upload")
    a = write_pdf(tmp_path / "unit1.pdf", MATERIAL_A)
    b = write_pdf(tmp_path / "unit2.pdf", MATERIAL_B)

    with a.open("rb") as fa, b.open("rb") as fb:
        r = client.post(
            f"/courses/{course_id}/materials", headers=coordinator,
            files=[("files", ("unit1.pdf", fa, "application/pdf")),
                   ("files", ("unit2.pdf", fb, "application/pdf"))],
        )
    # the upload answers as soon as the bytes are safe; indexing is a job
    assert r.status_code == 202, r.text
    assert [j["filename"] for j in r.json()] == ["unit1.pdf", "unit2.pdf"]
    assert all(j["state"] == "queued" for j in r.json())

    done = _jobs(client, course_id, coordinator)
    assert {j["filename"]: j["state"] for j in done} == {"unit1.pdf": "done", "unit2.pdf": "done"}
    assert all(j["chunk_count"] >= 1 for j in done)
    assert len(client.get(f"/courses/{course_id}/materials", headers=coordinator).json()) == 2


def test_one_bad_file_does_not_discard_the_rest(client, new_course, tmp_path):
    coordinator = login(client, "coordinator@example.edu")
    course_id = new_course("BATCHBAD", "Batch With A Bad File")
    good = write_pdf(tmp_path / "good.pdf", MATERIAL_A)
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"this is not a PDF at all")
    notes = tmp_path / "notes.txt"
    notes.write_text("plain text is not accepted")

    with good.open("rb") as fg, broken.open("rb") as fb, notes.open("rb") as fn:
        r = client.post(
            f"/courses/{course_id}/materials", headers=coordinator,
            files=[("files", ("good.pdf", fg, "application/pdf")),
                   ("files", ("broken.pdf", fb, "application/pdf")),
                   ("files", ("notes.txt", fn, "text/plain"))],
        )
    assert r.status_code == 202

    states = {j["filename"]: j for j in _jobs(client, course_id, coordinator)}
    assert states["good.pdf"]["state"] == "done"
    assert states["broken.pdf"]["state"] == "failed"
    assert states["notes.txt"]["state"] == "rejected"
    assert "only .pdf and .docx" in states["notes.txt"]["detail"]

    # the one that failed leaves nothing behind: a material with no passages is worse than none
    materials = client.get(f"/courses/{course_id}/materials", headers=coordinator).json()
    assert [m["filename"] for m in materials] == ["good.pdf"]


def test_the_same_file_twice_is_not_indexed_twice(client, new_course, tmp_path):
    """Re-uploading an overlapping folder is the normal case, not the exception, and embedding the
    same passages again would crowd the retrieval index with duplicates of itself."""
    coordinator = login(client, "coordinator@example.edu")
    course_id = new_course("BATCHDUP", "Duplicate Upload")
    pdf = write_pdf(tmp_path / "unit.pdf", MATERIAL_A)

    with pdf.open("rb") as f:
        client.post(f"/courses/{course_id}/materials", headers=coordinator,
                    files=[("files", ("unit.pdf", f, "application/pdf"))])
    first = client.get(f"/courses/{course_id}/materials", headers=coordinator).json()
    assert len(first) == 1

    # same bytes, different name
    with pdf.open("rb") as f:
        r = client.post(f"/courses/{course_id}/materials", headers=coordinator,
                        files=[("files", ("unit-copy.pdf", f, "application/pdf"))])
    job = r.json()[0]
    assert job["state"] == "duplicate"
    assert "already indexed as unit.pdf" in job["detail"]

    after = client.get(f"/courses/{course_id}/materials", headers=coordinator).json()
    assert len(after) == 1 and after[0]["chunk_count"] == first[0]["chunk_count"]


def test_an_empty_batch_is_refused(client, new_course):
    coordinator = login(client, "coordinator@example.edu")
    course_id = new_course("BATCHNONE", "No Files")
    r = client.post(f"/courses/{course_id}/materials", headers=coordinator, files=[])
    assert r.status_code in (400, 422)


def test_the_queue_is_scoped_to_the_subject(client, new_course, tmp_path):
    coordinator = login(client, "coordinator@example.edu")
    mine = new_course("QUEUEMINE", "Mine")
    theirs = new_course("QUEUETHEIRS", "Theirs", members=False)
    pdf = write_pdf(tmp_path / "unit.pdf", MATERIAL_A)
    with pdf.open("rb") as f:
        client.post(f"/courses/{theirs}/materials", headers=coordinator,
                    files=[("files", ("secret.pdf", f, "application/pdf"))])

    reviewer = login(client, "reviewer1@example.edu")
    assert client.get(f"/courses/{mine}/ingestion-jobs", headers=reviewer).json() == []
    assert client.get(f"/courses/{theirs}/ingestion-jobs", headers=reviewer).status_code == 404
