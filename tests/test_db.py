"""Persistence tests: schema, unique index, tag CRUD."""

from __future__ import annotations

import sqlite3

import pytest

from swotvac_saviour import db


@pytest.fixture
def conn(tmp_path):
    c = db.connect(str(tmp_path / "test.db"))
    db.init_db(c)
    yield c
    c.close()


def test_schema_tables_exist(conn):
    assert db.table_names(conn) == set(db.TABLE_NAMES)


def test_questions_unique_per_document_section(tmp_path):
    """Labels may repeat across sections (numbered booklets); never within one section."""
    conn = db.connect(str(tmp_path / "u.db"))
    try:
        db.init_db(conn)
        import uuid

        sha = uuid.uuid4().hex
        doc_id = db.insert_document(conn, "f.pdf", sha, "/tmp/f.pdf")
        db.insert_question(
            conn, doc_id, page_number=1, section="Ch.1", label="Question 1.",
            body="a", body_latex="a", hint=None,
        )
        db.insert_question(
            conn, doc_id, page_number=5, section="Ch.2", label="Question 1.",
            body="b", body_latex="b", hint=None,
        )
        conn.commit()
        # same section + same label -> rejected by the scoped unique index
        with pytest.raises(sqlite3.IntegrityError):
            db.insert_question(
                conn, doc_id, page_number=9, section="Ch.1", label="Question 1.",
                body="c", body_latex="c", hint=None,
            )
        conn.rollback()
        conn.commit()
        counts = conn.execute(
            "SELECT section, label, COUNT(*) FROM questions "
            "GROUP BY section, label"
        ).fetchall()
        assert dict((r[0], r[2]) for r in counts) == {"Ch.1": 1, "Ch.2": 1}
    finally:
        conn.close()

def test_previous_question_reopens_last_answered(conn):
    # queue: q1, q2 answered; previous makes q2 current and un-attempts it.
    import swotvac_saviour.db as db

    doc = db.insert_document(conn, "d.pdf", "sha-prev", "/tmp/d.pdf")
    q1 = db.insert_question(conn, doc, page_number=1, section="", label="Q1.",
                            body="a", body_latex="a", hint=None, warnings=[],
                            engine="pymupdf", confidence=None, solution="")
    q2 = db.insert_question(conn, doc, page_number=2, section="", label="Q2.",
                            body="b", body_latex="b", hint=None, warnings=[],
                            engine="pymupdf", confidence=None, solution="")
    db.append_to_queue(conn, [q1, q2])
    db.advance_queue(conn, q1)
    db.advance_queue(conn, q2)

    got = db.previous_question(conn)
    assert got == q2
    current = db.current_queue_question(conn)
    assert current["question_id"] == q2
    state = db.question_state(conn, q2)
    assert not state["attempted"]
    assert not state["marked"]
    # q1 stays answered
    assert db.queue_counts(conn)["answered"] == 1


def test_previous_with_nothing_answered(conn):
    import swotvac_saviour.db as db

    assert db.previous_question(conn) is None


def test_set_document_play_removes_and_restores(conn):
    import swotvac_saviour.db as db

    doc2 = db.insert_document(conn, "d2.pdf", "sha2", "/tmp/d2.pdf")
    for i in range(3):
        db.insert_question(conn, doc2, page_number=1, section="",
                           label=f"Q{i}.", body="b", body_latex="b",
                           hint=None, warnings=[], engine="pymupdf",
                           confidence=None, solution="")
    ids = [r[0] for r in conn.execute(
        "SELECT id FROM questions ORDER BY id").fetchall()]
    db.append_to_queue(conn, ids)
    db.advance_queue(conn, ids[0])

    db.set_document_play(conn, doc2, enabled=False)
    assert conn.execute("SELECT COUNT(*) FROM queue").fetchone()[0] == 0
    assert db.list_documents(conn)[0]["play_enabled"] is False

    db.set_document_play(conn, doc2, enabled=True)
    # all three were unanswered, so all return to the tail
    assert conn.execute("SELECT COUNT(*) FROM queue").fetchone()[0] == 3
    assert db.list_documents(conn)[0]["play_enabled"] is True
