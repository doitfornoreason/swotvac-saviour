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