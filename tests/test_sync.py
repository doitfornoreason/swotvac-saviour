"""sync_directory pipeline: LLM rows, sha gate, retag on rename, fallback."""

from __future__ import annotations

import json

import pytest

import swotvac_saviour.sync as sync_mod
from swotvac_saviour import db, transcribe
from swotvac_saviour import transcribe as transcribe_mod


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


def _make_spanning_pdf(path):
    """Two pages: Exercise 1.1 starts p1 and continues onto p2."""
    import fitz

    doc = fitz.open()
    p1 = doc.new_page()
    p1.insert_text((72, 700), "Exercise 1.1. first part of the question")
    p2 = doc.new_page()
    p2.insert_text((72, 72), "continuation line on page two")
    p2.insert_text((72, 200), "Exercise 1.2. second question")
    doc.save(str(path))
    doc.close()


def test_sync_uses_thread_pool_and_keeps_order(tmp_path, key):
    """Admitted questions transcribe concurrently but insert in parse order."""
    import threading
    import time as time_mod

    root = _study_dir(tmp_path, name="Pool Check")
    booklet = root / "Pool Check" / "booklet.pdf"
    booklet.unlink()
    _make_pdf(booklet, [f"Exercise 1.{i}." for i in range(1, 7)])

    threads: set[str] = set()

    def slow_call(messages, model, k):
        # Real ModelCall contract: takes the message list, returns JSON text.
        prompt = messages[0]["content"][0]["text"]
        label = prompt.split('exactly "')[1].split('"')[0]
        threads.add(threading.current_thread().name)
        time_mod.sleep(0.05)
        return json.dumps(_fake_transcribe(label, b"", model=model, key=k))

    conn = _connect(tmp_path)
    try:
        result = sync_mod.sync_directory(conn, root, key=key, call=slow_call)
        assert result.llm == 6
        assert len(threads) > 1, "expected multiple worker threads"
        rows = conn.execute(
            "SELECT label FROM questions ORDER BY id"
        ).fetchall()
        assert [r[0] for r in rows] == [
            "Exercise 1.1.", "Exercise 1.2.", "Exercise 1.3.",
            "Exercise 1.4.", "Exercise 1.5.", "Exercise 1.6.",
        ]
    finally:
        conn.close()


def test_sync_crop_includes_continuation_pages(tmp_path, key, monkeypatch):
    """A question spanning a page break gets a stacked crop with the tail."""
    from swotvac_saviour import transcribe as tr

    root = _study_dir(tmp_path, name="Span Check")
    booklet = root / "Span Check" / "booklet.pdf"
    booklet.unlink()
    _make_spanning_pdf(booklet)

    seen: dict[str, list] = {}

    real_crop = tr.crop_png_bytes

    def spy_crop(pdf_path, page_number, start_label, end_label, dpi=150,
                 continuation=None):
        seen[start_label] = (page_number, end_label, continuation)
        return real_crop(pdf_path, page_number, start_label, end_label,
                         dpi=dpi, continuation=continuation)

    monkeypatch.setattr(tr, "crop_png_bytes", spy_crop)

    def fake(label, crop, *, model, key, call=None, retries=1):
        return _fake_transcribe(label, crop, model=model, key=key)

    monkeypatch.setattr(tr, "transcribe_question", fake)

    conn = _connect(tmp_path)
    try:
        result = sync_mod.sync_directory(conn, root, key=key)
        assert result.llm == 2
    finally:
        conn.close()

    # Exercise 1.1 spans pages: continuation segment for page 2, bounded by
    # the next label; Exercise 1.2 (last question) gets none.
    assert seen["Exercise 1.1."][0] == 1
    assert seen["Exercise 1.1."][2] == [(2, "Exercise 1.2.")]
    assert seen["Exercise 1.2."][2] in (None, [])


def test_crop_png_bytes_stacks_continuation(tmp_path):
    """Continuation segments render below the primary crop in one PNG."""
    import fitz
    from swotvac_saviour import transcribe as tr

    path = tmp_path / "span.pdf"
    _make_spanning_pdf(path)

    single = tr.crop_png_bytes(str(path), 1, "Exercise 1.1.", None)
    stacked = tr.crop_png_bytes(
        str(path), 1, "Exercise 1.1.", None,
        continuation=[(2, "Exercise 1.2.")],
    )
    import pymupdf

    def png_height(blob):
        with pymupdf.open(stream=blob, filetype="png") as d:
            return d[0].rect.height

    assert png_height(stacked) > png_height(single)

    import struct

    def png_size(blob):
        # IHDR at fixed offset; rect() depends on embedded dpi metadata.
        w, h = struct.unpack(">II", blob[16:24])
        return w, h

    assert png_size(stacked)[0] == png_size(single)[0]


def test_sync_last_question_gets_continuation(tmp_path, key, monkeypatch):
    """A trailing question whose body spans pages is no longer cut at the
    first page bottom: segments run to the parser's end_page."""
    import fitz
    from swotvac_saviour import transcribe as tr

    root = _study_dir(tmp_path, name="Tail Check")
    booklet = root / "Tail Check" / "booklet.pdf"
    booklet.unlink()
    doc = fitz.open()
    p1 = doc.new_page()
    p1.insert_text((72, 700), "Exercise 1.1. starts on page one")
    p2 = doc.new_page()
    p2.insert_text((72, 72), "and ends on page two.")
    doc.save(str(booklet))
    doc.close()

    seen: dict[str, list] = {}
    real_crop = tr.crop_png_bytes

    def spy_crop(pdf_path, page_number, start_label, end_label, dpi=150,
                 continuation=None):
        seen[start_label] = continuation
        return real_crop(pdf_path, page_number, start_label, end_label,
                         dpi=dpi, continuation=continuation)

    monkeypatch.setattr(tr, "crop_png_bytes", spy_crop)
    monkeypatch.setattr(
        tr, "transcribe_question",
        lambda label, crop, *, model, key, call=None, retries=1:
            _fake_transcribe(label, crop, model=model, key=key),
    )

    conn = _connect(tmp_path)
    try:
        result = sync_mod.sync_directory(conn, root, key=key)
        assert result.llm == 1
    finally:
        conn.close()
    assert seen["Exercise 1.1."] == [(2, None)]


def test_crop_skips_unresolvable_continuation_bound(tmp_path):
    """A continuation page whose end label can't be found is skipped, not
    stacked as a full page (which would swallow the next question)."""
    import fitz
    import pymupdf
    from swotvac_saviour import transcribe as tr

    path = tmp_path / "span.pdf"
    _make_spanning_pdf(path)

    single = tr.crop_png_bytes(str(path), 1, "Exercise 1.1.", None)
    skipped = tr.crop_png_bytes(
        str(path), 1, "Exercise 1.1.", None,
        continuation=[(2, "No Such Label")],
    )

    def png_size(blob):
        import struct
        return struct.unpack(">II", blob[16:24])

    assert png_size(skipped) == png_size(single)


def test_updated_document_replaces_bank(tmp_path, key, transcribed):
    """Same folder + same filename + different content = replacement, not append."""
    root = _study_dir(tmp_path)  # booklet.pdf with Exercise 1.1/1.2
    conn = _connect(tmp_path)
    try:
        assert sync_mod.sync_directory(conn, root, key=key).imported == ["booklet.pdf"]
        # "update" the file: same name, different content
        booklet = root / "Functional Analysis" / "booklet.pdf"
        booklet.unlink()
        _make_pdf(booklet, ["Exercise 1.1.", "Exercise 1.2.", "Exercise 1.3."])
        conn.execute("UPDATE queue SET answered = 1")
        conn.commit()
        result = sync_mod.sync_directory(conn, root, key=key)
        assert result.replaced == ["booklet.pdf"]
        assert result.imported == ["booklet.pdf"]
        n = conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
        labels = [r[0] for r in conn.execute("SELECT label FROM questions ORDER BY id")]
        assert n == 3 and labels == ["Exercise 1.1.", "Exercise 1.2.", "Exercise 1.3."]
        # old queue rows died with the old questions; new ones appended
        answered = conn.execute("SELECT COUNT(*) FROM queue WHERE answered=1").fetchone()[0]
        assert answered == 0
    finally:
        conn.close()


def test_same_name_in_other_folder_is_new_document(tmp_path, key, transcribed):
    root = _study_dir(tmp_path)
    other = root / "DEs"
    other.mkdir()
    _make_pdf(other / "booklet.pdf", ["Exercise 2.1."])
    conn = _connect(tmp_path)
    try:
        result = sync_mod.sync_directory(conn, root, key=key)
        assert result.replaced == []
        assert sorted(result.imported) == ["booklet.pdf", "booklet.pdf"]
    finally:
        conn.close()


def test_force_resync_replaces_unchanged_document(tmp_path, key, monkeypatch):
    """Per-document re-sync: sha unchanged, bank still wiped and re-done."""
    root = _study_dir(tmp_path)
    conn = _connect(tmp_path)
    try:
        sync_mod.sync_directory(conn, root, key=key)
        before = conn.execute("SELECT id FROM documents").fetchone()[0]
        calls = []
        real_transcribe = transcribe_mod.transcribe_question

        def counting(label, crop, *, model, key, call=None, retries=1):
            calls.append(label)
            return _fake_transcribe(label, crop, model=model, key=key)

        monkeypatch.setattr(transcribe_mod, "transcribe_question", counting)
        pdf = root / "Functional Analysis" / "booklet.pdf"
        result = sync_mod.sync_directory(conn, root, key=key, only=pdf, force=True)
        assert result.replaced == ["booklet.pdf"]
        assert result.skipped == []
        assert len(calls) == 2  # re-transcribed
        assert conn.execute("SELECT id FROM documents").fetchone()[0] != before
    finally:
        conn.close()
