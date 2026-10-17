"""Seeded practice sessions: create, step, record, summarize."""

from __future__ import annotations

import json
import random
import sqlite3
from collections.abc import Sequence

ALLOWED_RESULTS = ("known", "retry")


class NoQuestionsError(ValueError):
    """The selected tags match no questions."""


def create_session(
    conn: sqlite3.Connection,
    tag_ids: Sequence[int],
    *,
    seed: int | None = None,
    source_session_id: int | None = None,
) -> tuple[int, int]:
    """Create a session over the DISTINCT pool of tagged questions.

    Returns (session_id, seed_used). ``seed=None`` draws a fresh concrete int
    (random.Random(None) is not retrievable afterwards, so we always store the
    int that shuffled the pool). ``source_session_id`` restricts the pool to
    that session's retry questions ("play missed again").
    """
    if source_session_id is not None:
        questions = _retry_question_ids(conn, source_session_id)
    else:
        questions = _pool_question_ids(conn, tag_ids)
    if not questions:
        raise NoQuestionsError("no questions match the selected tags")

    rng = random.Random()
    used_seed = seed if seed is not None else rng.randrange(2**63)
    rng.seed(used_seed)
    rng.shuffle(questions)

    cur = conn.execute(
        "INSERT INTO sessions (seed, config) VALUES (?, ?)",
        (
            used_seed,
            json.dumps(
                {
                    "tag_ids": [int(t) for t in tag_ids]
                    if source_session_id is None
                    else [],
                    "source_session_id": source_session_id,
                }
            ),
        ),
    )
    session_id = int(cur.lastrowid)
    conn.executemany(
        "INSERT INTO session_questions (session_id, question_id, position) VALUES (?, ?, ?)",
        [(session_id, qid, i) for i, qid in enumerate(questions)],
    )
    conn.commit()
    return session_id, used_seed


def _pool_question_ids(conn: sqlite3.Connection, tag_ids: Sequence[int]) -> list[int]:
    if not tag_ids:
        return []
    marks = ",".join("?" for _ in tag_ids)
    rows = conn.execute(
        f"""
        SELECT DISTINCT q.id FROM questions q
        JOIN document_tags dt ON dt.document_id = q.document_id
        WHERE dt.tag_id IN ({marks})
        ORDER BY q.id
        """,
        tuple(tag_ids),
    ).fetchall()
    return [r[0] for r in rows]


def _retry_question_ids(conn: sqlite3.Connection, session_id: int) -> list[int]:
    rows = conn.execute(
        """
        SELECT question_id FROM session_questions
        WHERE session_id = ? AND result = 'retry'
        ORDER BY position
        """,
        (session_id,),
    ).fetchall()
    return [r[0] for r in rows]


def current_question(conn: sqlite3.Connection, session_id: int) -> dict | None:
    """The next unanswered question, or None when the deck is exhausted."""
    row = conn.execute(
        """
        SELECT q.id, q.document_id, q.page_number, q.section, q.label,
               q.body_latex, q.hint, d.filename,
               sq.position AS answered,
               (SELECT COUNT(*) FROM session_questions WHERE session_id = ?) AS total,
               (SELECT COALESCE(MAX(position), -1) FROM session_questions
                WHERE session_id = ? AND result = 'retry') AS last_retry
        FROM session_questions sq
        JOIN questions q ON q.id = sq.question_id
        JOIN documents d ON d.id = q.document_id
        WHERE sq.session_id = ? AND sq.result IS NULL
        ORDER BY sq.position LIMIT 1
        """,
        (session_id, session_id, session_id),
    ).fetchone()
    if row is None:
        return None
    answered = row[8]
    # Deck is answered in position order and results are immutable, so the
    # streak is the trailing run of knowns: all answered positions after the
    # last retry. answered - 1 - last_retry (0-based positions).
    trailing = answered - 1 - row[10]
    return {
        "question_id": row[0],
        "document_id": row[1],
        "page_number": row[2],
        "section": row[3],
        "label": row[4],
        "body_latex": row[5],
        "hint": row[6],
        "document": row[7],
        "answered": answered,
        "total": row[9],
        "streak": trailing if trailing >= 0 else 0,
    }


def record_result(
    conn: sqlite3.Connection, session_id: int, question_id: int, result: str
) -> bool:
    """Record a result. Idempotent: returns False when already answered."""
    if result not in ALLOWED_RESULTS:
        raise ValueError(f"result must be one of {ALLOWED_RESULTS}")
    cur = conn.execute(
        "UPDATE session_questions SET result = ? "
        "WHERE session_id = ? AND question_id = ? AND result IS NULL",
        (result, session_id, question_id),
    )
    conn.commit()
    return cur.rowcount > 0


def summary(conn: sqlite3.Connection, session_id: int) -> dict | None:
    row = conn.execute(
        "SELECT id, seed, created_at, finished FROM sessions WHERE id = ?",
        (session_id,),
    ).fetchone()
    if row is None:
        return None
    counts = conn.execute(
        """
        SELECT result, COUNT(*) FROM session_questions
        WHERE session_id = ? GROUP BY result
        """,
        (session_id,),
    ).fetchall()
    tally = dict(counts)
    stats = conn.execute(
        """
        SELECT q.section, result, COUNT(*)
        FROM session_questions sq JOIN questions q ON q.id = sq.question_id
        WHERE sq.session_id = ? AND sq.result IS NOT NULL
        GROUP BY q.section, result
        """,
        (session_id,),
    ).fetchall()
    return {
        "id": row[0],
        "seed": row[1],
        "created_at": row[2],
        "finished": row[3],
        "known": tally.get("known", 0),
        "retry": tally.get("retry", 0),
        "by_section": stats,
    }


def finish(conn: sqlite3.Connection, session_id: int) -> None:
    conn.execute(
        "UPDATE sessions SET finished = datetime('now') WHERE id = ? AND finished IS NULL",
        (session_id,),
    )
    conn.commit()