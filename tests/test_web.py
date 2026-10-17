"""Web shell tests: dashboard, upload, review, tags, error paths."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from swotvac_saviour.web import create_app

REPO_ROOT = Path(__file__).resolve().parent.parent
EXERCISES = REPO_ROOT / "samples" / "exercises.pdf"
MAST20030 = REPO_ROOT / "samples" / "MAST20030ProblemBooklet_Topic4.pdf"


@pytest.fixture
def client(tmp_path):
    app = create_app(str(tmp_path / "web.db"))
    with TestClient(app) as c:
        yield c


def _create_tag(client, name):
    return client.post("/tags", data={"name": name}, follow_redirects=False)


def _upload(client, path, tags=(), style="exercise"):
    for t in tags:
        _create_tag(client, t)
    data = {"style": style}
    if tags:
        data["tags"] = list(tags) if len(tags) > 1 else tags[0]
    files = {"pdf": (path.name, open(path, "rb"), "application/pdf")}
    return client.post("/upload", data=data, files=files, follow_redirects=False)


def test_dashboard_empty(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "No documents yet" in r.text


def test_upload_redirects_to_review(client):
    r = _upload(client, EXERCISES, tags=["Functional Analysis"])
    assert r.status_code == 303
    loc = r.headers["location"]
    assert loc.startswith("/documents/1/review")


def test_review_lists_all_questions(client):
    _upload(client, EXERCISES)
    r = client.get("/documents/1/review")
    assert r.status_code == 200
    assert "Exercise 1.1." in r.text
    assert "Exercise B.16." in r.text
    assert "122 questions" in r.text


def test_review_shows_warnings_on_numbered_sample(client):
    # MAST20030 has a guaranteed "solutions section" warning
    r = _upload(client, MAST20030, style="number")
    assert r.status_code == 303
    r = client.get(r.headers["location"].split("?")[0])
    assert r.status_code == 200
    assert "solutions section starts at page" in r.text
    assert "7 questions" in r.text


def test_upload_stores_pdf_by_sha(client):
    _upload(client, EXERCISES)
    stored = list((Path(client.app.state.uploads_dir)).glob("*.pdf"))
    assert len(stored) == 1
    assert len(stored[0].stem) == 64  # sha256 hex, client filename never used


def test_reupload_same_sha_skips(client):
    _upload(client, EXERCISES)
    r = _upload(client, EXERCISES, tags=["Algebra"])
    assert r.status_code == 303
    assert r.headers["location"].startswith("/documents/1/review?outcome=skipped")
    docs = client.get("/").text
    assert docs.count("exercises.pdf") == 1


def test_upload_rejects_empty_file(client):
    files = {"pdf": ("empty.pdf", b"", "application/pdf")}
    r = client.post("/upload", data={"style": "exercise"}, files=files)
    assert r.status_code == 400
    assert not list((Path(client.app.state.uploads_dir)).glob("*.pdf"))


def test_upload_rejects_missing_filename(client):
    r = client.post(
        "/upload",
        data={"style": "exercise"},
        files={"pdf": ("", b"%PDF", "application/pdf")},
    )
    assert r.status_code in (400, 422)


def test_upload_rejects_non_pdf(client):
    files = {"pdf": ("notes.txt", b"this is not a pdf", "text/plain")}
    r = client.post("/upload", data={"style": "exercise"}, files=files)
    assert r.status_code == 400
    assert not list((Path(client.app.state.uploads_dir)).glob("*.pdf"))


def test_tag_questions_listing(client):
    _upload(client, EXERCISES, tags=["Functional Analysis"])
    tag_id = 1
    r = client.get(f"/tags/{tag_id}/questions")
    assert r.status_code == 200
    assert "Exercise 1.1." in r.text
    assert "Exercise B.16." in r.text


def test_tag_page_lists_tags(client):
    _upload(client, EXERCISES, tags=["Functional Analysis"])
    r = client.get("/tags")
    assert r.status_code == 200
    assert "Functional Analysis" in r.text


def test_missing_document_404(client):
    r = client.get("/documents/999/review")
    assert r.status_code == 404


def test_missing_tag_404(client):
    r = client.get("/tags/999/questions")
    assert r.status_code == 404


def test_assign_tag_fragment_vs_redirect(client):
    _upload(client, EXERCISES, tags=["Algebra"])
    # duplicate create returns 409, never 500
    assert _create_tag(client, "Algebra").status_code == 409
    # htmx request -> fragment
    r = client.post(
        "/documents/1/tags",
        data={"tag_name": "Algebra"},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 200
    assert "Algebra" in r.text
    # unknown tag -> 400, no auto-create
    r = client.post(
        "/documents/1/tags",
        data={"tag_name": "Topology"},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 400
    # plain form -> redirect
    r = client.post(
        "/documents/1/tags", data={"tag_name": "Algebra"}, follow_redirects=False
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/documents/1/review"


def test_reimport_changes_style(client):
    _upload(client, MAST20030, style="number")
    r = client.get("/documents/1/review")
    assert "7 questions" in r.text
    # re-import with default exercise style (no numbered labels -> 0 questions)
    r = client.post(
        "/documents/1/reimport", data={"style": "exercise"}, follow_redirects=False
    )
    assert r.status_code == 303
    r = client.get("/documents/1/review")
    assert "No questions extracted" in r.text


def test_duplicate_tag_409_never_500(client, monkeypatch):
    from swotvac_saviour import db as swotvac_db

    def boom(conn, name):
        raise swotvac_db.DuplicateTagError(name)

    monkeypatch.setattr(swotvac_db, "add_tag", boom)
    # /tags create surfaces DuplicateTagError as 409, never 500
    r = client.post("/tags", data={"name": "X"}, follow_redirects=False)
    assert r.status_code == 409
    assert "tag already exists" in r.text
    with open(EXERCISES, "rb") as fh:
        r = client.post(
            "/upload",
            data={"style": "exercise"},
            files={"pdf": ("exercises.pdf", fh, "application/pdf")},
            follow_redirects=False,
        )
    assert r.status_code == 303  # untagged import still works, no 500


def test_upload_rejects_unknown_tag(client):
    files = {"pdf": ("exercises.pdf", open(EXERCISES, "rb").read(), "application/pdf")}
    r = client.post(
        "/upload",
        data={"style": "exercise", "tags": "NoSuchTag"},
        files=files,
        follow_redirects=False,
    )
    assert r.status_code == 400
    assert "unknown tag" in r.text
    assert not list((Path(client.app.state.uploads_dir)).glob("*.pdf"))


def test_create_tag_route(client):
    r = _create_tag(client, "Algebra")
    assert r.status_code == 303
    r2 = _create_tag(client, "Algebra")
    assert r2.status_code == 409
    assert client.get("/tags").status_code == 200
    assert "Algebra" in client.get("/tags").text


def test_upload_rejects_text_file_pymupdf_can_open(client):
    """PyMuPDF can open plain text; the magic-byte gate must reject it."""
    form_feed = b"foo\n\x0cbar\n\x0cbaz\n"
    files = {"pdf": ("fake.txt", form_feed, "text/plain")}
    r = client.post(
        "/upload", data={"style": "exercise"}, files=files, follow_redirects=False
    )
    assert r.status_code == 400
    assert not list((Path(client.app.state.uploads_dir)).glob("*.pdf"))


def test_import_pdf_rejects_non_pdf(tmp_path):
    from swotvac_saviour.importer import import_pdf

    fake = tmp_path / "fake.txt"
    fake.write_bytes(b"not a pdf at all\n\x0cmore")
    with pytest.raises(ValueError, match="not a PDF"):
        import_pdf(str(tmp_path / "x.db"), str(fake), [], style="exercise")


def test_static_files_served(client):
    r = client.get("/static/style.css")
    assert r.status_code == 200
    assert "font-family" in r.text
    r = client.get("/static/htmx.min.js")
    assert r.status_code == 200
    assert r.text.startswith("var htmx")