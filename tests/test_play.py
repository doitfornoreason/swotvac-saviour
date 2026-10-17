"""Play mode tests: sessions, shuffle, streak, answer flow, katex, page images."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from swotvac_saviour import db, normalise, session
from swotvac_saviour.importer import import_pdf
from swotvac_saviour.web import create_app

REPO_ROOT = Path(__file__).resolve().parent.parent
EXERCISES = REPO_ROOT / "samples" / "exercises.pdf"
KATEX_DIR = (
    REPO_ROOT / "src" / "swotvac_saviour" / "static" / "katex"
)


@pytest.fixture
def seeded(tmp_path):
    """DB with exercises.pdf imported and tagged."""
    conn = db.connect(str(tmp_path / "s.db"))
    try:
        db.init_db(conn)
        db.add_tag(conn, "Analysis")
        conn.commit()
        import_pdf(str(tmp_path / "s.db"), str(EXERCISES), ["Analysis"])
        conn = db.connect(str(tmp_path / "s.db"))
        tag_id = db.tag_id(conn, "Analysis")
        yield conn, tmp_path, tag_id
    finally:
        conn.close()


@pytest.fixture
def client(tmp_path):
    app = create_app(str(tmp_path / "web.db"))
    with TestClient(app) as c:
        yield c


def _import_and_tag(client, tags=("Analysis",)):
    for t in tags:
        client.post("/tags", data={"name": t}, follow_redirects=False)
    with open(EXERCISES, "rb") as fh:
        r = client.post(
            "/upload",
            data={"style": "exercise", "tags": list(tags) if len(tags) > 1 else tags[0]},
            files={"pdf": ("exercises.pdf", fh, "application/pdf")},
            follow_redirects=False,
        )
    assert r.status_code == 303
    return tags


# --- session engine ---------------------------------------------------------


def test_pool_distinct_overlapping_tags(seeded):
    conn, tmp, first = seeded
    second = db.add_tag(conn, "Second")
    doc_id = conn.execute("SELECT id FROM documents LIMIT 1").fetchone()[0]
    db.assign_tag(conn, doc_id, second)
    conn.commit()
    sid, _ = session.create_session(conn, [first, second])
    counts = conn.execute(
        "SELECT COUNT(*) FROM session_questions WHERE session_id = ?", (sid,)
    ).fetchone()[0]
    assert counts == 122  # no duplicated rows from the overlapping tags


def test_seed_determinism(seeded):
    conn, _tmp, tag_id = seeded
    sid_a, seed_a = session.create_session(conn, [tag_id], seed=42)
    sid_b, seed_b = session.create_session(conn, [tag_id], seed=42)
    assert seed_a == seed_b == 42
    order_a = [r[0] for r in conn.execute(
        "SELECT question_id FROM session_questions WHERE session_id = ? ORDER BY position",
        (sid_a,),
    )]
    order_b = [r[0] for r in conn.execute(
        "SELECT question_id FROM session_questions WHERE session_id = ? ORDER BY position",
        (sid_b,),
    )]
    assert order_a == order_b


def test_seed_none_stores_concrete_int(seeded):
    conn, _tmp, tag_id = seeded
    sid, seed = session.create_session(conn, [tag_id])
    assert isinstance(seed, int) and 0 <= seed < 2**63
    stored = conn.execute(
        "SELECT seed FROM sessions WHERE id = ?", (sid,)
    ).fetchone()[0]
    assert stored == seed


def test_empty_pool_raises(seeded):
    conn, _tmp, _tag_id = seeded
    conn.execute("DELETE FROM questions")
    conn.commit()
    with pytest.raises(session.NoQuestionsError):
        session.create_session(conn, [])


def test_streak_break(seeded):
    conn, _tmp, tag_id = seeded
    sid, _ = session.create_session(conn, [tag_id], seed=7)
    qs = [r[0] for r in conn.execute(
        "SELECT question_id FROM session_questions WHERE session_id = ? ORDER BY position",
        (sid,),
    )]
    assert session.current_question(conn, sid)["streak"] == 0
    session.record_result(conn, sid, qs[0], "known")
    session.record_result(conn, sid, qs[1], "known")
    assert session.current_question(conn, sid)["streak"] == 2
    session.record_result(conn, sid, qs[2], "retry")
    assert session.current_question(conn, sid)["streak"] == 0  # broken by retry


def test_double_submit_idempotent(seeded):
    conn, _tmp, tag_id = seeded
    sid, _ = session.create_session(conn, [tag_id])
    qid = conn.execute(
        "SELECT question_id FROM session_questions WHERE session_id = ? LIMIT 1", (sid,)
    ).fetchone()[0]
    assert session.record_result(conn, sid, qid, "known") is True
    assert session.record_result(conn, sid, qid, "known") is False  # already answered
    row = conn.execute(
        "SELECT result FROM session_questions WHERE session_id = ? AND question_id = ?",
        (sid, qid),
    ).fetchone()[0]
    assert row == "known"


def test_result_vocabulary(seeded):
    conn, _tmp, tag_id = seeded
    sid, _ = session.create_session(conn, [tag_id])
    qid = conn.execute(
        "SELECT question_id FROM session_questions WHERE session_id = ? LIMIT 1", (sid,)
    ).fetchone()[0]
    with pytest.raises(ValueError):
        session.record_result(conn, sid, qid, "maybe")


def test_retry_session_from_source(seeded):
    conn, _tmp, tag_id = seeded
    sid, _ = session.create_session(conn, [tag_id], seed=3)
    qs = [r[0] for r in conn.execute(
        "SELECT question_id FROM session_questions WHERE session_id = ? ORDER BY position",
        (sid,),
    )]
    session.record_result(conn, sid, qs[0], "known")
    session.record_result(conn, sid, qs[1], "retry")
    sid2, _ = session.create_session(conn, [], source_session_id=sid)
    pool = [r[0] for r in conn.execute(
        "SELECT question_id FROM session_questions WHERE session_id = ?", (sid2,)
    )]
    assert pool == [qs[1]]


# --- katex preprocessing -----------------------------------------------------


def test_latex_segments_wrap_prose_and_keep_math():
    segs = normalise.latex_segments(
        r"Show that \mid d(x,y) -d(t,y)\mid\leq d(x,t) \in X."
    )
    joined = "".join(text for _, text in segs)
    assert joined.count("{") == joined.count("}")
    kinds, texts = zip(*segs)
    assert kinds == (
        "prose", "math", "prose", "math", "math", "prose", "math", "prose",
    )
    assert texts == (
        "Show that ", "\\mid", " d(x,y) -d(t,y)", "\\mid", "\\leq",
        " d(x,t) ", "\\in", " X.",
    )


def test_latex_segments_all_bodies_balanced():
    from swotvac_saviour.importer import parse_questions

    for q in parse_questions(str(EXERCISES), "exercise"):
        blob = "".join(text for _, text in normalise.latex_segments(q["body_latex"]))
        assert blob.count("{") == blob.count("}"), q["label"]


def test_katex_fonts_all_served(client):
    css = (KATEX_DIR / "katex.min.css").read_text()
    refs = sorted(set(re.findall(r"url\(fonts/([^)]*)\)", css)))
    assert refs, "no font references found"
    for ref in refs:
        r = client.get(f"/static/katex/fonts/{ref}")
        assert r.status_code == 200, ref


# --- web play flow -----------------------------------------------------------


def test_page_image_png(client):
    _import_and_tag(client)
    r = client.get("/documents/1/page/5")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(r.content) > 1000


def test_page_image_out_of_range(client):
    _import_and_tag(client)
    assert client.get("/documents/1/page/99999").status_code == 404
    assert client.get("/documents/1/page/0").status_code == 404


def test_empty_pool_400(client):
    r = client.post("/sessions", data={}, follow_redirects=False)
    assert r.status_code in (400, 303)
    # no tags selected -> empty pool -> 400
    r = client.post("/sessions", data={"tags": []}, follow_redirects=False)
    assert r.status_code == 400


def test_unknown_tag_400(client):
    _import_and_tag(client)
    r = client.post("/sessions", data={"tags": "999"}, follow_redirects=False)
    assert r.status_code == 400


def test_play_flow_to_summary(client):
    _import_and_tag(client)
    r = client.post(
        "/sessions", data={"tags": "1"}, follow_redirects=False
    )
    assert r.status_code == 303
    sid = int(r.headers["location"].split("/")[2])
    r = client.get(f"/sessions/{sid}/play")
    assert r.status_code == 200
    assert 'class="katex-inline"' in r.text
    total = 122
    for _ in range(total):
        # single question answers until the deck is exhausted
        page = client.get(f"/sessions/{sid}/play")
        assert page.status_code == 200
        qid = int(re.search(r'name="question_id" value="(\d+)"', page.text).group(1))
        r = client.post(
            f"/sessions/{sid}/answer",
            data={"question_id": qid, "result": "known"},
            follow_redirects=False,
        )
        if "summary" in r.headers.get("location", ""):
            break
    r = client.get(f"/sessions/{sid}/summary")
    assert r.status_code == 200
    assert "Session summary" in r.text
    assert "Practice the 0 missed questions again" in r.text


def test_finished_session_redirects_to_summary(client):
    _import_and_tag(client)
    r = client.post("/sessions", data={"tags": "1"}, follow_redirects=False)
    sid = int(r.headers["location"].split("/")[2])
    # answer every question
    while True:
        page = client.get(f"/sessions/{sid}/play", follow_redirects=False)
        if page.status_code == 303 or "summary" in str(page.url):
            break
        qid = int(re.search(r'name="question_id" value="(\d+)"', page.text).group(1))
        r = client.post(
            f"/sessions/{sid}/answer",
            data={"question_id": qid, "result": "known"},
            follow_redirects=False,
        )
        if "summary" in r.headers.get("location", ""):
            break
    # next play request auto-finishes and redirects
    r = client.get(f"/sessions/{sid}/play", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].endswith("/summary")


def test_stop_redirects_to_summary(client):
    _import_and_tag(client)
    r = client.post("/sessions", data={"tags": "1"}, follow_redirects=False)
    sid = int(r.headers["location"].split("/")[2])
    r = client.post(f"/sessions/{sid}/stop", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].endswith("/summary")


def test_missing_session_404(client):
    assert client.get("/sessions/999/play").status_code == 404
    assert client.get("/sessions/999/summary").status_code == 404


def test_stats_page(client):
    _import_and_tag(client)
    client.post("/sessions", data={"tags": "1"}, follow_redirects=False)
    r = client.get("/stats")
    assert r.status_code == 200
    assert "Accuracy by tag" in r.text
    assert "Analysis" in r.text