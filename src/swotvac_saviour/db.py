"""SQLite persistence: schema, connections, and row helpers."""

from __future__ import annotations

import json
import random
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
    solution TEXT,
    attempted INTEGER NOT NULL DEFAULT 0,
    marked INTEGER NOT NULL DEFAULT 0,
    flagged INTEGER NOT NULL DEFAULT 0,
    engine TEXT,
    confidence REAL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_questions_document_section_label
    ON questions(document_id, section, label);

-- Global practice queue. ``position`` orders play; ``answered`` marks a row as
-- past (played). Past positions are immutable: reshuffles only touch rows with
-- answered = 0.
CREATE TABLE IF NOT EXISTS queue (
    question_id INTEGER PRIMARY KEY REFERENCES questions(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    answered INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_queue_position ON queue(position);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

TABLE_NAMES: tuple[str, ...] = (
    "documents",
    "tags",
    "document_tags",
    "questions",
    "queue",
    "settings",
)

NO_SOLUTION = "no solutions - feature not added yet"


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


# --- settings ----------------------------------------------------------------

def get_setting(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


# --- documents / questions ----------------------------------------------------

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
    engine: str | None = None,
    confidence: float | None = None,
    solution: str = NO_SOLUTION,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO questions
            (document_id, page_number, section, label, body, body_latex, hint,
             warnings, engine, confidence, solution)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            engine,
            confidence,
            solution,
        ),
    )
    return int(cur.lastrowid)


def add_tag(conn: sqlite3.Connection, name: str) -> int:
    try:
        cur = conn.execute("INSERT INTO tags (name) VALUES (?)", (name,))
    except sqlite3.IntegrityError as err:
        raise DuplicateTagError(name) from err
    return int(cur.lastrowid)


def add_tag_or_get(conn: sqlite3.Connection, name: str) -> int:
    """Return the tag id, creating the tag when missing (sync auto-create)."""
    tid = tag_id(conn, name)
    if tid is not None:
        return tid
    return add_tag(conn, name)


def tag_options(conn: sqlite3.Connection) -> list[dict]:
    """Tags as (id, name) dicts for browse filters."""
    rows = conn.execute("SELECT id, name FROM tags ORDER BY name").fetchall()
    return [{"id": r[0], "name": r[1]} for r in rows]


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


def get_question(conn: sqlite3.Connection, question_id: int) -> dict | None:
    """One question with document attribution and state flags."""
    row = conn.execute(
        """
        SELECT q.id, q.document_id, q.page_number, q.section, q.label,
               q.body, q.body_latex, q.hint, q.warnings,
               q.attempted, q.marked, q.flagged,
               d.filename
        FROM questions q JOIN documents d ON d.id = q.document_id
        WHERE q.id = ?
        """,
        (question_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "id": row[0],
        "document_id": row[1],
        "page_number": row[2],
        "section": row[3],
        "label": row[4],
        "body": row[5],
        "body_latex": row[6],
        "hint": row[7],
        "warnings": json.loads(row[8]),
        "attempted": bool(row[9]),
        "marked": bool(row[10]),
        "flagged": bool(row[11]),
        "document": row[12],
    }


# --- question state ------------------------------------------------------------

_STATE_COLUMNS = ("attempted", "marked", "flagged")


def set_question_state(
    conn: sqlite3.Connection,
    question_id: int,
    *,
    attempted: bool | None = None,
    marked: bool | None = None,
    flagged: bool | None = None,
) -> dict | None:
    """Update state flags. Marking a question also marks it attempted.

    Returns the new state dict, or None when the question does not exist.
    """
    row = conn.execute(
        "SELECT attempted, marked, flagged FROM questions WHERE id = ?", (question_id,)
    ).fetchone()
    if row is None:
        return None
    state = dict(zip(_STATE_COLUMNS, (bool(row[0]), bool(row[1]), bool(row[2]))))
    updates: dict[str, bool] = {}
    for key, value in (
        ("attempted", attempted),
        ("marked", marked),
        ("flagged", flagged),
    ):
        if value is not None:
            updates[key] = value
    if "marked" in updates and updates["marked"]:
        updates["attempted"] = True
    if not updates:
        return state
    sets = ", ".join(f"{key} = ?" for key in updates)
    conn.execute(
        f"UPDATE questions SET {sets} WHERE id = ?",
        (*[int(v) for v in updates.values()], question_id),
    )
    conn.commit()
    state.update(updates)
    return state


def question_state(conn: sqlite3.Connection, question_id: int) -> dict | None:
    row = conn.execute(
        "SELECT attempted, marked, flagged FROM questions WHERE id = ?", (question_id,)
    ).fetchone()
    if row is None:
        return None
    return dict(zip(_STATE_COLUMNS, (bool(row[0]), bool(row[1]), bool(row[2]))))


# --- queue ---------------------------------------------------------------------

def append_to_queue(conn: sqlite3.Connection, question_ids: Sequence[int]) -> None:
    """Append questions to the tail of the queue as unanswered."""
    row = conn.execute("SELECT COALESCE(MAX(position), -1) FROM queue").fetchone()
    next_pos = int(row[0]) + 1
    conn.executemany(
        "INSERT INTO queue (question_id, position, answered) VALUES (?, ?, 0)",
        [(qid, next_pos + i) for i, qid in enumerate(question_ids)],
    )


def current_queue_question(conn: sqlite3.Connection) -> dict | None:
    """The first unanswered queue entry with its question, or None when done."""
    row = conn.execute(
        """
        SELECT q.id, q.document_id, q.page_number, q.section, q.label,
               q.body_latex, q.hint, d.filename,
               queue.position,
               (SELECT COUNT(*) FROM queue) AS total,
               (SELECT COUNT(*) FROM queue WHERE answered = 1) AS answered
        FROM queue
        JOIN questions q ON q.id = queue.question_id
        JOIN documents d ON d.id = q.document_id
        WHERE queue.answered = 0
        ORDER BY queue.position
        LIMIT 1
        """,
    ).fetchone()
    if row is None:
        return None
    return {
        "question_id": row[0],
        "document_id": row[1],
        "page_number": row[2],
        "section": row[3],
        "label": row[4],
        "body_latex": row[5],
        "hint": row[6],
        "document": row[7],
        "position": row[8],
        "total": row[9],
        "answered": row[10],
    }


def advance_queue(conn: sqlite3.Connection, question_id: int) -> bool:
    """Mark the current queue row answered. Idempotent; only unanswered rows."""
    cur = conn.execute(
        "UPDATE queue SET answered = 1 WHERE question_id = ? AND answered = 0",
        (question_id,),
    )
    conn.commit()
    return cur.rowcount > 0


def reshuffle_queue(
    conn: sqlite3.Connection, seed: int | None = None
) -> tuple[int, int]:
    """Shuffle current+future (unanswered) queue rows; past order stays fixed.

    Returns (reshuffled_count, seed_used). Positions are reassigned contiguously
    after the last answered position.
    """
    rng = random.Random()
    used_seed = seed if seed is not None else rng.randrange(2**63)
    rng.seed(used_seed)
    pending = [
        r[0]
        for r in conn.execute(
            "SELECT question_id FROM queue WHERE answered = 0 ORDER BY position"
        ).fetchall()
    ]
    if not pending:
        return 0, used_seed
    rng.shuffle(pending)
    max_answered = conn.execute(
        "SELECT COALESCE(MAX(position), -1) FROM queue WHERE answered = 1"
    ).fetchone()[0]
    base = int(max_answered) + 1
    conn.executemany(
        "UPDATE queue SET position = ? WHERE question_id = ?",
        [(base + i, qid) for i, qid in enumerate(pending)],
    )
    conn.commit()
    return len(pending), used_seed


def requeue_flagged(conn: sqlite3.Connection) -> int:
    """Re-open answered queue rows whose question is flagged or marked."""
    cur = conn.execute(
        """
        UPDATE queue SET answered = 0
        WHERE answered = 1 AND question_id IN (
            SELECT id FROM questions WHERE flagged = 1 OR marked = 1
        )
        """,
    )
    conn.commit()
    return cur.rowcount


def queue_counts(conn: sqlite3.Connection) -> dict:
    row = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(answered), 0) FROM queue"
    ).fetchone()
    return {"total": int(row[0]), "answered": int(row[1])}


# --- browse ---------------------------------------------------------------------

def browse_questions(
    conn: sqlite3.Connection,
    *,
    tag_id: int | None = None,
    source_document_id: int | None = None,
    flagged: bool = False,
    attempted_unmarked: bool = False,
) -> list[dict]:
    """Questions matching the browse filters, ordered by document then id."""
    where: list[str] = []
    params: list = []
    if tag_id is not None:
        where.append(
            "q.document_id IN (SELECT document_id FROM document_tags WHERE tag_id = ?)"
        )
        params.append(tag_id)
    if source_document_id is not None:
        where.append("q.document_id = ?")
        params.append(source_document_id)
    if flagged:
        where.append("q.flagged = 1")
    if attempted_unmarked:
        where.append("q.attempted = 1 AND q.marked = 0")
    sql = f"""
        SELECT q.id, q.document_id, q.page_number, q.section, q.label,
               q.body_latex, q.hint, d.filename,
               q.attempted, q.marked, q.flagged
        FROM questions q
        JOIN documents d ON d.id = q.document_id
        {"WHERE " + " AND ".join(where) if where else ""}
        ORDER BY q.document_id, q.id
    """
    rows = conn.execute(sql, tuple(params)).fetchall()
    return [
        {
            "id": r[0],
            "document_id": r[1],
            "page_number": r[2],
            "section": r[3],
            "label": r[4],
            "body_latex": r[5],
            "hint": r[6],
            "document": r[7],
            "attempted": bool(r[8]),
            "marked": bool(r[9]),
            "flagged": bool(r[10]),
        }
        for r in rows
    ]
