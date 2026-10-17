"""Persistence tests: schema, round-trip, idempotency, tag CRUD."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from swotvac_saviour import db, normalise, parser
from swotvac_saviour.ingestion import load_pdf

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE = REPO_ROOT / "samples" / "exercises.pdf"


@pytest.fixture
def conn(tmp_path):
    c = db.connect(str(tmp_path / "test.db"))
    db.init_db(c)
    yield c
    c.close()


def test_schema_tables_exist(conn):
    assert db.table_names(conn) == set(db.TABLE_NAMES)


def _fresh_questions() -> list[dict]:
    doc = load_pdf(str(SAMPLE))
    qs = parser.split_questions(doc)
    for q in qs:
        q.body_latex = normalise.to_latex(q.body)
    return [q.to_dict() for q in qs]


def test_round_trip_matches_fresh_parse(tmp_path):
    """Import exercises.pdf; DB rows must equal a fresh parse field-for-field."""
    from swotvac_saviour.importer import import_pdf

    db_path = str(tmp_path / "t.db")
    doc_id, outcome = import_pdf(db_path, str(SAMPLE), [], style="exercise")
    assert outcome == "created"
    assert doc_id == 1

    conn = db.connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT page_number, section, label, body, body_latex, hint, warnings
            FROM questions ORDER BY id
            """
        ).fetchall()
        from_db = [
            {
                "page_number": r[0],
                "section": r[1],
                "label": r[2],
                "body": r[3],
                "body_latex": r[4],
                "hint": r[5],
                "warnings": json.loads(r[6]),
            }
            for r in rows
        ]
    finally:
        conn.close()

    expected = _fresh_questions()
    assert len(from_db) == len(expected) == 122
    for got, want in zip(from_db, expected):
        assert got["label"] == want["label"]
        assert got["section"] == want["section"]
        assert got["page_number"] == want["page"]
        assert got["body"] == want["body"]
        assert got["body_latex"] == want["body_latex"]
        assert got["hint"] == want["hint"]
        assert got["warnings"] == want["warnings"]


def test_import_idempotent(tmp_path):
    import hashlib

    from swotvac_saviour.importer import import_pdf

    db_path = str(tmp_path / "t.db")
    _, outcome = import_pdf(db_path, str(SAMPLE), [], style="exercise")
    assert outcome == "created"
    _, outcome2 = import_pdf(db_path, str(SAMPLE), [], style="exercise")
    assert outcome2 == "skipped"  # second run: skip, still succeeds

    with open(SAMPLE, "rb") as fh:
        sha = hashlib.sha256(fh.read()).hexdigest()
    conn = db.connect(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0] == 122
        assert db.document_by_sha(conn, sha) is not None
    finally:
        conn.close()


def test_tag_crud_unique_and_assign(tmp_path):
    from swotvac_saviour.importer import import_pdf

    db_path = str(tmp_path / "t.db")
    conn = db.connect(db_path)
    try:
        db.init_db(conn)
        db.add_tag(conn, "Functional Analysis")
        conn.commit()
    finally:
        conn.close()
    _, outcome = import_pdf(db_path, str(SAMPLE), ["Functional Analysis"], style="exercise")
    assert outcome == "created"

    conn = db.connect(db_path)
    try:
        assert db.list_tags(conn) == ["Functional Analysis"]
        doc_id = conn.execute("SELECT id FROM documents").fetchone()[0]
        assert db.tags_for_document(conn, doc_id) == ["Functional Analysis"]

        with pytest.raises(db.DuplicateTagError):
            db.add_tag(conn, "Functional Analysis")

        second = db.add_tag(conn, "Algebra")
        db.assign_tag(conn, doc_id, second)
        assert db.tags_for_document(conn, doc_id) == ["Algebra", "Functional Analysis"]
        # duplicate assignment is a no-op
        db.assign_tag(conn, doc_id, second)
        assert db.tags_for_document(conn, doc_id) == ["Algebra", "Functional Analysis"]
    finally:
        conn.close()


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