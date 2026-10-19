"""sync_directory pipeline: LLM rows, sha gate, retag on rename, fallback."""

from __future__ import annotations

import pytest

import swotvac_saviour.sync as sync_mod
from swotvac_saviour import db, transcribe


def _make_pdf(path, labels):
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    y = 72
    for label in labels:
        page.insert_text((72, y), f"{label} Question text for {label}")
        y += 48
    doc.save(str(path))
    doc.close()


def _study_dir(tmp_path, name="Functional Analysis"):
    root = tmp_path / "study"
    subject = root / name
    subject.mkdir(parents=True)
    _make_pdf(subject / "booklet.pdf", ["Exercise 1.1.", "Exercise 1.2."])
    return root


@pytest.fixture
def key():
    return "sk-or-test"


def _fake_transcribe(label, crop, *, model, key, call=None, retries=1):
    return {
        "label": label,
        "body": f"body {label}",
        "body_latex": f"body {label}",
        "confidence": 0.9,
    }


@pytest.fixture
def transcribed(monkeypatch):
    calls = []

    def fake(label, crop, *, model, key, call=None, retries=1):
        calls.append(label)
        return _fake_transcribe(label, crop, model=model, key=key, call=call)

    monkeypatch.setattr(transcribe, "transcribe_question", fake)
    return calls


def _connect(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    db.init_db(conn)
    return conn


def test_sync_inserts_llm_rows_and_queue(tmp_path, key, transcribed):
    root = _study_dir(tmp_path)
    conn = _connect(tmp_path)
    try:
        result = sync_mod.sync_directory(conn, root, model="m/x", cap=0, key=key)
        assert result.imported == ["booklet.pdf"]
        assert result.llm == 2 and result.fallback == 0
        assert transcribed == ["Exercise 1.1.", "Exercise 1.2."]
        rows = conn.execute(
            """
            SELECT label, body, body_latex, engine, confidence, hint, solution
            FROM questions ORDER BY id
            """
        ).fetchall()
        assert [r[0] for r in rows] == ["Exercise 1.1.", "Exercise 1.2."]
        assert all(r[1] == f"body {r[0]}" for r in rows)
        assert all(r[3] == "llm" and r[4] == 0.9 for r in rows)
        assert all(r[5] is None for r in rows)  # hints only from PDF text
        assert all(r[6] == db.NO_SOLUTION for r in rows)
        assert db.list_tags(conn) == ["Functional Analysis"]
        assert db.queue_counts(conn)["total"] == 2
        assert "1 imported, 0 skipped; 2 questions (2 llm, 0 fallback)" in result.summary()
    finally:
        conn.close()


def test_sync_sha_gate_skips_resync(tmp_path, key, transcribed):
    root = _study_dir(tmp_path)
    conn = _connect(tmp_path)
    try:
        sync_mod.sync_directory(conn, root, cap=0, key=key)
        again = sync_mod.sync_directory(conn, root, cap=0, key=key)
        assert again.imported == [] and again.skipped == ["booklet.pdf"]
        assert conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0] == 2
        assert len(transcribed) == 2  # no second LLM pass
    finally:
        conn.close()


def test_sync_retags_renamed_folder(tmp_path, key, transcribed):
    root = _study_dir(tmp_path)
    conn = _connect(tmp_path)
    try:
        sync_mod.sync_directory(conn, root, cap=0, key=key)
        (root / "Functional Analysis").rename(root / "Real Analysis")
        result = sync_mod.sync_directory(conn, root, cap=0, key=key)
        assert result.retagged == ["booklet.pdf"]
        # sha gate skipped the PDF; current folder tag re-attached. The old
        # tag stays (known Phase 4 limitation until full reconciliation).
        assert db.list_tags(conn) == ["Functional Analysis", "Real Analysis"]
        doc_id = conn.execute("SELECT id FROM documents").fetchone()[0]
        assert db.tags_for_document(conn, doc_id) == [
            "Functional Analysis", "Real Analysis",
        ]
    finally:
        conn.close()


def test_sync_falls_back_when_llm_fails(tmp_path, key, monkeypatch):
    root = _study_dir(tmp_path)
    conn = _connect(tmp_path)

    def broken(label, crop, *, model, key, call=None, retries=1):
        raise transcribe.TranscriptionError("guard rails")

    monkeypatch.setattr(transcribe, "transcribe_question", broken)
    try:
        result = sync_mod.sync_directory(conn, root, cap=0, key=key)
        assert result.fallback == 2 and result.llm == 0
        engines = [r[0] for r in conn.execute(
            "SELECT engine FROM questions ORDER BY id")]
        assert engines == ["pymupdf", "pymupdf"]
        warnings = conn.execute("SELECT warnings FROM questions").fetchall()
        assert all("llm transcription failed" in w[0] for w in warnings)
    finally:
        conn.close()


def test_sync_cost_cap_keeps_deterministic_tail(tmp_path, key, transcribed):
    root = _study_dir(tmp_path)
    conn = _connect(tmp_path)
    try:
        result = sync_mod.sync_directory(conn, root, cap=1, key=key)
        assert result.llm == 1 and result.uncapped == 1
        rows = conn.execute(
            "SELECT engine, warnings FROM questions ORDER BY id").fetchall()
        assert rows[0][0] == "llm"
        assert rows[1][0] == "pymupdf"
        assert "cost cap reached" in rows[1][1]
    finally:
        conn.close()
