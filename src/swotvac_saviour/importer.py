"""Shared PDF import logic used by both the CLI and the web layer."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Any

from . import db
from . import normalise, parser
from .ingestion import load_pdf

Outcome = str  # one of: "created", "skipped", "replaced"


class NoSuchTagError(ValueError):
    """Raised when a tag name in the request does not exist."""


def _validate_tags(conn, tags: list[str]) -> list[int]:
    """Resolve tag names; raise NoSuchTagError for any that do not exist."""
    ids: list[int] = []
    for name in tags:
        tid = db.tag_id(conn, name)
        if tid is None:
            raise NoSuchTagError(name)
        ids.append(tid)
    return ids


def sha256_file(path: str) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def parse_questions(pdf_path: str, style: str) -> list[dict[str, Any]]:
    """Parse a PDF into question dicts (body_latex normalised)."""
    doc = load_pdf(pdf_path)
    questions = parser.split_questions(doc, style=style)
    for q in questions:
        q.body_latex = normalise.to_latex(q.body)
    return [q.to_dict() for q in questions]


def import_pdf(
    db_path: str,
    pdf_path: str,
    tags: list[str],
    style: str = "exercise",
    *,
    replace: bool = False,
    filename: str | None = None,
    sha: str | None = None,
) -> tuple[int | None, Outcome]:
    """Import a PDF into the database. Returns (document_id, outcome).

    outcome is "created" (new document), "skipped" (sha already present,
    newly requested tags still assigned), or "replaced" (existing document's
    questions deleted and re-inserted in one transaction).

    ``filename`` names the document for display; defaults to the pdf_path
    basename (the web layer passes the client's original filename since it
    stores uploads under a sha256-derived path). ``sha`` skips the second
    full read when the caller already hashed the file (web upload streams and
    hashes in one pass).
    """
    display_name = filename or Path(pdf_path).name
    with open(pdf_path, "rb") as fh:
        if fh.read(5) != b"%PDF-":
            raise ValueError(f"not a PDF: {pdf_path}")
    conn = db.connect(db_path)
    try:
        db.init_db(conn)
        tag_ids = _validate_tags(conn, tags)  # fail before hashing or writing
        sha = sha or sha256_file(pdf_path)
        existing = db.document_by_sha(conn, sha)
        if existing is None:
            document_id = db.insert_document(
                conn, display_name, sha, str(Path(pdf_path).resolve())
            )
            _insert_questions(conn, document_id, parse_questions(pdf_path, style))
            outcome: Outcome = "created"
        elif replace:
            document_id = existing
            db.replace_document_questions(
                conn, document_id, parse_questions(pdf_path, style)
            )
            outcome = "replaced"
        else:
            document_id = existing
            outcome = "skipped"
        for tag in tag_ids:
            db.assign_tag(conn, document_id, tag)
        conn.commit()
        return document_id, outcome
    except sqlite3.IntegrityError:
        conn.rollback()
        raise
    finally:
        conn.close()


def _insert_questions(
    conn: sqlite3.Connection, document_id: int, questions: list[dict[str, Any]]
) -> None:
    for q in questions:
        db.insert_question(
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