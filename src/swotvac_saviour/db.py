"""SQLite persistence: schema, connections, and row helpers."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL,
    sha256 TEXT NOT NULL UNIQUE,
    uploaded_at TEXT NOT NULL DEFAULT (datetime('now')),
    original_path TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS document_tags (
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (document_id, tag_id)
);

CREATE TABLE IF NOT EXISTS questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    page_number INTEGER NOT NULL,
    section TEXT NOT NULL DEFAULT '',
    label TEXT NOT NULL,
    body TEXT NOT NULL,
    body_latex TEXT NOT NULL,
    hint TEXT,
    warnings TEXT NOT NULL DEFAULT '[]',
    solution TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_questions_document_section_label
    ON questions(document_id, section, label);

CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    seed INTEGER NOT NULL,
    config TEXT NOT NULL DEFAULT '{}',
    finished TEXT
);

CREATE TABLE IF NOT EXISTS session_questions (
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    result TEXT,
    PRIMARY KEY (session_id, question_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_session_questions_position
    ON session_questions(session_id, position);
"""

TABLE_NAMES: tuple[str, ...] = (
    "documents",
    "tags",
    "document_tags",
    "questions",
    "sessions",
    "session_questions",
)


class DuplicateTagError(ValueError):
    """Raised when a tag name already exists."""


def connect(path: str) -> sqlite3.Connection:
    """Open a database and enforce foreign keys."""
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Create all tables; safe to call repeatedly."""
    conn.executescript(SCHEMA)
    conn.commit()


def table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    ).fetchall()
    # sqlite_sequence is created implicitly by AUTOINCREMENT columns
    return {r[0] for r in rows if not r[0].startswith("sqlite_")}


def insert_document(conn: sqlite3.Connection, filename: str, sha256: str, path: str) -> int:
    cur = conn.execute(
        "INSERT INTO documents (filename, sha256, original_path) VALUES (?, ?, ?)",
        (filename, sha256, path),
    )
    return int(cur.lastrowid)


def document_by_sha(conn: sqlite3.Connection, sha256: str) -> int | None:
    row = conn.execute(
        "SELECT id FROM documents WHERE sha256 = ?", (sha256,)
    ).fetchone()
    return int(row[0]) if row else None


def insert_question(
    conn: sqlite3.Connection,
    document_id: int,
    *,
    page_number: int,
    section: str,
    label: str,
    body: str,
    body_latex: str,
    hint: str | None,
    warnings: Sequence[str] = (),
) -> int:
    cur = conn.execute(
        """
        INSERT INTO questions
            (document_id, page_number, section, label, body, body_latex, hint, warnings)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            document_id,
            page_number,
            section,
            label,
            body,
            body_latex,
            hint,
            json.dumps(list(warnings)),
        ),
    )
    return int(cur.lastrowid)


def add_tag(conn: sqlite3.Connection, name: str) -> int:
    try:
        cur = conn.execute("INSERT INTO tags (name) VALUES (?)", (name,))
    except sqlite3.IntegrityError as err:
        raise DuplicateTagError(name) from err
    return int(cur.lastrowid)


def list_tags(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT name FROM tags ORDER BY name").fetchall()
    return [r[0] for r in rows]


def assign_tag(conn: sqlite3.Connection, document_id: int, tag_id: int) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO document_tags (document_id, tag_id) VALUES (?, ?)",
        (document_id, tag_id),
    )


def tags_for_document(conn: sqlite3.Connection, document_id: int) -> list[str]:
    rows = conn.execute(
        """
        SELECT tags.name FROM tags
        JOIN document_tags ON document_tags.tag_id = tags.id
        WHERE document_tags.document_id = ?
        ORDER BY tags.name
        """,
        (document_id,),
    ).fetchall()
    return [r[0] for r in rows]


def tag_id(conn: sqlite3.Connection, name: str) -> int | None:
    row = conn.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()
    return int(row[0]) if row else None


def list_documents(conn: sqlite3.Connection) -> list[dict]:
    """Documents with id, filename, sha, upload time, tags and question count."""
    rows = conn.execute(
        """
        SELECT d.id, d.filename, d.sha256, d.uploaded_at,
               (SELECT COUNT(*) FROM questions q WHERE q.document_id = d.id)
        FROM documents d ORDER BY d.id
        """
    ).fetchall()
    # one join pass for all tags instead of N+1 queries
    tag_rows = conn.execute(
        """
        SELECT dt.document_id, t.name
        FROM document_tags dt JOIN tags t ON t.id = dt.tag_id
        ORDER BY dt.document_id, t.name
        """
    ).fetchall()
    tags_by_doc: dict[int, list[str]] = {}
    for doc_id, name in tag_rows:
        tags_by_doc.setdefault(doc_id, []).append(name)
    return [
        {
            "id": r[0],
            "filename": r[1],
            "sha256": r[2],
            "uploaded_at": r[3],
            "question_count": r[4],
            "tags": tags_by_doc.get(r[0], []),
        }
        for r in rows
    ]


def questions_for_document(conn: sqlite3.Connection, document_id: int) -> list[dict]:
    """All questions of a document, ordered by insertion (document order)."""
    rows = conn.execute(
        """
        SELECT id, page_number, section, label, body, body_latex, hint, warnings
        FROM questions WHERE document_id = ? ORDER BY id
        """,
        (document_id,),
    ).fetchall()
    return [
        {
            "id": r[0],
            "page_number": r[1],
            "section": r[2],
            "label": r[3],
            "body": r[4],
            "body_latex": r[5],
            "hint": r[6],
            "warnings": json.loads(r[7]),
        }
        for r in rows
    ]


def questions_for_tag(conn: sqlite3.Connection, tag_id: int) -> list[dict]:
    """All questions whose document carries the given tag."""
    rows = conn.execute(
        """
        SELECT q.id, q.page_number, q.section, q.label,
               q.body_latex, q.hint, d.filename, d.id
        FROM questions q
        JOIN documents d ON d.id = q.document_id
        JOIN document_tags dt ON dt.document_id = d.id
        WHERE dt.tag_id = ?
        ORDER BY q.id
        """,
        (tag_id,),
    ).fetchall()
    return [
        {
            "id": r[0],
            "page_number": r[1],
            "section": r[2],
            "label": r[3],
            "body_latex": r[4],
            "hint": r[5],
            "document": r[6],
            "document_id": r[7],
        }
        for r in rows
    ]


def replace_document_questions(
    conn: sqlite3.Connection, document_id: int, questions: list
) -> None:
    """Delete and re-insert a document's questions in one transaction."""
    conn.execute("DELETE FROM questions WHERE document_id = ?", (document_id,))
    for q in questions:
        insert_question(
            conn,
            document_id,
            page_number=q["page"],
            section=q["section"],
            label=q["label"],
            body=q["body"],
            body_latex=q["body_latex"],
            hint=q["hint"],
            warnings=q["warnings"],
        )