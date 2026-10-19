"""Web shell tests: dashboard/sync, play queue, browse filters, state toggles."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from swotvac_saviour import db
from swotvac_saviour.sync import SyncResult
from swotvac_saviour.web import create_app

REPO_ROOT = Path(__file__).resolve().parent.parent
EXERCISES = REPO_ROOT / "samples" / "exercises.pdf"


@pytest.fixture
def conn(tmp_path):
    c = db.connect(str(tmp_path / "seed.db"))
    db.init_db(c)
    yield c
    c.close()


@pytest.fixture
def client(tmp_path):
    app = create_app(str(tmp_path / "web.db"))
    with TestClient(app) as c:
        yield c


def _create_tag(client, name):
    return client.post("/tags", data={"name": name}, follow_redirects=False)


def _seed_document(conn, *, tag_name="Functional Analysis", n=2, flagged=(), marked=()):
    """One document + n questions appended to the queue; returns question ids."""
    tag_id = db.add_tag(conn, tag_name)
    doc_id = db.insert_document(conn, "test.pdf", "sha-test-" + tag_name, "/tmp/test.pdf")
    db.assign_tag(conn, doc_id, tag_id)
    conn.commit()
    ids = []
    for i in range(n):
        qid = db.insert_question(
            conn, doc_id,
            page_number=i + 1, section="", label=f"Exercise 1.{i + 1}.",
            body=f"body {i}", body_latex=f"body {i}", hint=None,
        )
        ids.append(qid)
    conn.commit()
    db.append_to_queue(conn, ids)
    conn.commit()
    for idx in flagged:
        db.set_question_state(conn, ids[idx], flagged=True)
    for idx in marked:
        db.set_question_state(conn, ids[idx], marked=True)
    return doc_id, ids


# --- dashboard ------------------------------------------------------------------

def test_dashboard_empty(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "No documents yet" in r.text
    assert "Sync study folder" in r.text


def test_dashboard_renders_counts(client, conn):
    conn2 = db.connect(str(client.app.state.db_path))
    db.init_db(conn2)
    _seed_document(conn2)
    conn2.close()
    r = client.get("/")
    assert r.status_code == 200
    assert "0 of 2 questions answered" in r.text


# --- sync -----------------------------------------------------------------------

def _fake_sync_result(n=3):
    return SyncResult(imported=["s.pdf"], questions=n, llm=n, fallback=0)


def test_sync_requires_dir(client):
    r = client.post("/sync", data={"dir": "", "model": "", "cap": "0"}, follow_redirects=False)
    assert r.status_code == 400


def test_sync_runs_and_renders_dashboard(client):
    with patch("swotvac_saviour.sync.sync_directory", return_value=_fake_sync_result()) as mock_sync, \
         patch("swotvac_saviour.transcribe.load_api_key", return_value="sk-or-test"):
        r = client.post(
            "/sync",
            data={"dir": ".", "model": "test-model", "cap": "0"},
            follow_redirects=False,
        )
        assert r.status_code == 303

        # Sync runs in a background thread; wait for it to finish (the patched
        # sync_directory returns immediately, so this completes near-instantly).
        # The poll stays inside the patch context: the thread only starts after
        # the response returns, so an exited mock would fall through to the
        # real sync_directory and hang on samples/.
        for _ in range(50):
            if client.app.state.sync_job["done"]:
                break
            time.sleep(0.1)
    assert client.app.state.sync_job["done"]
    assert mock_sync.call_count == 1
    assert client.app.state.sync_job["summary"] is not None
    assert client.app.state.sync_job["error"] is None


def test_sync_fresh_renders_confirmation(client):
    with patch("swotvac_saviour.transcribe.load_api_key", return_value="sk-or-test"):
        r = client.post(
            "/sync", data={"dir": ".", "model": "", "cap": "0", "fresh": "1"},
            follow_redirects=False,
        )
    assert r.status_code == 200
    assert "Confirm fresh sync" in r.text or "Fresh sync" in r.text
    assert "wipe all" in r.text.lower() or "wipes" in r.text.lower()


def test_sync_confirm_requires_token(client):
    r = client.post(
        "/sync/confirm", data={"dir": ".", "model": "", "cap": "0", "fresh": "1", "token": "bad"},
        follow_redirects=False,
    )
    assert r.status_code == 409


def test_sync_confirm_with_valid_token(client):
    # first request generates and stores the token
    with patch("swotvac_saviour.transcribe.load_api_key", return_value="sk-or-test"):
        client.post("/sync", data={"dir": ".", "model": "", "cap": "0", "fresh": "1"}, follow_redirects=False)
    token = client.app.state.fresh_token
    with patch("swotvac_saviour.sync.sync_directory", return_value=_fake_sync_result()) as mock_sync, \
         patch("swotvac_saviour.transcribe.load_api_key", return_value="sk-or-test"):
        r = client.post(
            "/sync/confirm",
            data={"dir": ".", "model": "", "cap": "0", "fresh": "1", "token": token},
            follow_redirects=False,
        )
        assert r.status_code == 303

        for _ in range(50):
            if client.app.state.sync_job["done"]:
                break
            time.sleep(0.1)
    assert client.app.state.sync_job["done"]
    assert mock_sync.call_count == 1


# --- play -----------------------------------------------------------------------

def test_play_empty_renders_done(client):
    r = client.get("/play")
    assert r.status_code == 200
    assert "Queue done" in r.text


def test_play_shows_current_question(client, conn):
    conn2 = db.connect(str(client.app.state.db_path))
    db.init_db(conn2)
    _seed_document(conn2, n=1)
    conn2.close()
    r = client.get("/play")
    assert r.status_code == 200
    assert "Exercise 1.1." in r.text
    assert "no solutions" in r.text


def test_play_answer_advances_queue(client, conn):
    conn2 = db.connect(str(client.app.state.db_path))
    db.init_db(conn2)
    _, ids = _seed_document(conn2, n=1)
    conn2.close()
    r = client.post("/play/answer", data={"question_id": ids[0]}, follow_redirects=False)
    assert r.status_code in (200, 303)
    conn3 = db.connect(str(client.app.state.db_path))
    assert db.queue_counts(conn3)["answered"] == 1
    conn3.close()


def test_play_reshuffle_keeps_past_fixed(client, conn):
    conn2 = db.connect(str(client.app.state.db_path))
    db.init_db(conn2)
    _, ids = _seed_document(conn2, n=3)
    # answer the first one: it becomes past
    db.advance_queue(conn2, ids[0])
    before = conn2.execute("SELECT question_id, position FROM queue ORDER BY position").fetchall()
    conn2.close()
    r = client.post("/play/reshuffle", follow_redirects=False)
    assert r.status_code in (200, 303)
    conn3 = db.connect(str(client.app.state.db_path))
    after = conn3.execute("SELECT question_id, position FROM queue ORDER BY position").fetchall()
    conn3.close()
    # first (answered) row unchanged
    assert before[0] == after[0]
    # remaining two are a permutation of the same ids
    assert sorted(q for q, _ in after[1:]) == sorted(q for q, _ in before[1:])


def test_play_requeue_flagged(client, conn):
    conn2 = db.connect(str(client.app.state.db_path))
    db.init_db(conn2)
    _, ids = _seed_document(conn2, n=2, flagged=(0,))
    db.advance_queue(conn2, ids[0])
    db.advance_queue(conn2, ids[1])
    conn2.close()
    r = client.post("/play/requeue", follow_redirects=False)
    assert r.status_code in (200, 303)
    conn3 = db.connect(str(client.app.state.db_path))
    assert db.queue_counts(conn3)["answered"] == 1  # flagged one re-opened
    conn3.close()


# --- state toggles --------------------------------------------------------------

def test_state_mark_implies_attempted(client, conn):
    conn2 = db.connect(str(client.app.state.db_path))
    db.init_db(conn2)
    _, ids = _seed_document(conn2, n=1)
    conn2.close()
    r = client.post(
        "/questions/%d/state" % ids[0],
        data={"field": "marked", "value": "1", "next": "/play"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    state = db.question_state(db.connect(str(client.app.state.db_path)), ids[0])
    assert state == {"attempted": True, "marked": True, "flagged": False}


def test_state_rejects_unknown_field(client):
    r = client.post(
        "/questions/1/state", data={"field": "bogus", "value": "1"}, follow_redirects=False
    )
    assert r.status_code == 400


# --- browse ---------------------------------------------------------------------

def test_browse_lists_questions(client, conn):
    conn2 = db.connect(str(client.app.state.db_path))
    db.init_db(conn2)
    _seed_document(conn2, n=2)
    conn2.close()
    r = client.get("/browse")
    assert r.status_code == 200
    assert "Exercise 1.1." in r.text
    assert "Exercise 1.2." in r.text


def test_browse_filter_flagged(client, conn):
    conn2 = db.connect(str(client.app.state.db_path))
    db.init_db(conn2)
    _seed_document(conn2, n=2, flagged=(1,))
    conn2.close()
    r = client.get("/browse?flagged=1")
    assert r.status_code == 200
    assert "Exercise 1.2." in r.text
    assert "Exercise 1.1." not in r.text


def test_browse_filter_attempted_unmarked(client, conn):
    conn2 = db.connect(str(client.app.state.db_path))
    db.init_db(conn2)
    _seed_document(conn2, n=3, marked=(0,))
    # ids[0] is marked => attempted. ids[1] and ids[2] are not attempted.
    # explicitly attempt ids[1] only:
    db.set_question_state(conn2, 2, attempted=True)
    conn2.commit()
    conn2.close()
    r = client.get("/browse?unmarked=1")
    assert r.status_code == 200
    assert "Exercise 1.2." in r.text   # attempted but not marked
    assert "Exercise 1.1." not in r.text  # marked
    assert "Exercise 1.3." not in r.text  # not attempted


# --- question view --------------------------------------------------------------

def test_question_view_renders_details(client, conn):
    conn2 = db.connect(str(client.app.state.db_path))
    db.init_db(conn2)
    _, ids = _seed_document(conn2, n=1)
    conn2.close()
    r = client.get(f"/questions/{ids[0]}")
    assert r.status_code == 200
    assert "Exercise 1.1." in r.text
    assert "Warnings" in r.text


def test_question_view_404(client):
    r = client.get("/questions/9999")
    assert r.status_code == 404


# --- review / documents ---------------------------------------------------------

def test_review_lists_questions(client, conn):
    conn2 = db.connect(str(client.app.state.db_path))
    db.init_db(conn2)
    doc_id, _ = _seed_document(conn2, n=2)
    conn2.close()
    r = client.get(f"/documents/{doc_id}/review")
    assert r.status_code == 200
    assert "test.pdf" in r.text
    assert "Exercise 1.1." in r.text


def test_review_missing_document_404(client):
    r = client.get("/documents/999/review")
    assert r.status_code == 404


def test_page_image_route_missing_doc_404(client):
    r = client.get("/documents/999/page/1")
    assert r.status_code == 404


# --- tags -----------------------------------------------------------------------

def test_tag_page_lists_tags(client, conn):
    conn2 = db.connect(str(client.app.state.db_path))
    db.init_db(conn2)
    db.add_tag(conn2, "Algebra")
    conn2.commit()
    conn2.close()
    r = client.get("/tags")
    assert r.status_code == 200
    assert "Algebra" in r.text


def test_create_tag_route(client):
    r = client.post("/tags", data={"name": "Algebra"}, follow_redirects=False)
    assert r.status_code == 303
    conn = db.connect(str(client.app.state.db_path))
    assert "Algebra" in db.list_tags(conn)
    conn.close()


# --- static ---------------------------------------------------------------------

def test_static_files_served(client):
    r = client.get("/static/play.js")
    assert r.status_code == 200
    r = client.get("/static/style.css")
    assert r.status_code == 200


def test_base_template_has_new_nav(client):
    r = client.get("/")
    assert r.status_code == 200
    assert 'href="/play"' in r.text
    assert 'href="/browse"' in r.text
    assert 'href="/upload"' not in r.text
    assert 'href="/stats"' not in r.text


# --- setup -----------------------------------------------------------------------

def test_setup_page_shows_missing_key(tmp_path, monkeypatch):
    monkeypatch.setattr("swotvac_saviour.web.transcribe.load_api_key", lambda d: None)
    app = create_app(str(tmp_path / "web.db"), study_dir=tmp_path)
    with TestClient(app) as c:
        r = c.get("/setup")
    assert r.status_code == 200
    assert "No key found yet." in r.text
    assert str(tmp_path) in r.text


def test_setup_save_writes_env_and_redirects(tmp_path):
    study = tmp_path / "study"
    study.mkdir()
    app = create_app(str(tmp_path / "web.db"), study_dir=study)
    with TestClient(app) as c:
        r = c.post("/setup", data={"key": "sk-or-test", "model": ""},
                   follow_redirects=False)
        assert r.status_code == 303
        assert (study / ".env").read_text() == "OPENROUTER_API_KEY=sk-or-test\n"
        page = c.get("/setup")
        assert "Key found." in page.text


def test_setup_save_rejects_blank_key(tmp_path):
    app = create_app(str(tmp_path / "web.db"), study_dir=tmp_path)
    with TestClient(app) as c:
        r = c.post("/setup", data={"key": "  "})
    assert r.status_code == 400


def test_setup_save_without_study_dir(tmp_path):
    app = create_app(str(tmp_path / "web.db"))
    with TestClient(app) as c:
        r = c.post("/setup", data={"key": "sk-or-test"})
    assert r.status_code == 400
    assert "no study directory known" in r.text


def test_setup_save_sets_default_model(tmp_path):
    study = tmp_path / "study"
    study.mkdir()
    app = create_app(str(tmp_path / "web.db"), study_dir=study)
    with TestClient(app) as c:
        c.post("/setup", data={"key": "sk-or-test", "model": "other/model"})
    conn = db.connect(str(tmp_path / "web.db"))
    assert db.get_setting(conn, "model") == "other/model"
    conn.close()


# --- csrf guard ------------------------------------------------------------------

def test_post_with_mismatched_origin_rejected(client):
    r = client.post(
        "/play/answer", data={"question_id": "1"},
        headers={"Origin": "http://evil.example"},
    )
    assert r.status_code == 403
    assert "cross-origin" in r.text


def test_post_with_mismatched_referer_rejected(client):
    r = client.post(
        "/play/answer", data={"question_id": "1"},
        headers={"Referer": "http://evil.example/attack"},
    )
    assert r.status_code == 403


def test_post_same_origin_allowed(client):
    r = client.post(
        "/play/answer", data={"question_id": "1"},
        headers={"Origin": "http://testserver"},
    )
    assert r.status_code != 403


def test_post_without_origin_allowed(client):
    r = client.post("/play/answer", data={"question_id": "1"})
    assert r.status_code != 403


def test_get_with_foreign_origin_allowed(client):
    r = client.get("/", headers={"Origin": "http://evil.example"})
    assert r.status_code == 200
