"""Folder sync: subject folders -> tags, PDFs -> LLM-transcribed questions.

Layout: the study directory holds one folder per subject; each subject folder
holds that subject's question PDFs (recursively). The folder name becomes the
tag. Sync is idempotent via sha256: already-imported PDFs are skipped; a fresh
sync wipes documents/questions/state and re-parses everything.
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from . import db, normalise, parser, transcribe
from .ingestion import load_pdf

NO_SOLUTION = db.NO_SOLUTION


def sha256_file(path: str) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


@dataclass
class SyncResult:
    imported: list[str] = field(default_factory=list)  # display names
    skipped: list[str] = field(default_factory=list)
    retagged: list[str] = field(default_factory=list)  # sha-gate skips that re-attached a tag
    failed: list[str] = field(default_factory=list)  # unreadable PDFs
    questions: int = 0
    llm: int = 0
    fallback: int = 0
    uncapped: int = 0  # questions kept deterministic due to the cost cap
    fresh: bool = False

    def summary(self) -> str:
        parts = [f"{len(self.imported)} imported, {len(self.skipped)} skipped"]
        if self.retagged:
            parts.append(f"{len(self.retagged)} retagged")
        if self.fresh:
            parts.append("fresh wipe")
        parts.append(f"{self.questions} questions ({self.llm} llm, {self.fallback} fallback)")
        if self.uncapped:
            parts.append(f"{self.uncapped} kept deterministic by cost cap")
        if self.failed:
            parts.append(f"{len(self.failed)} failed: {', '.join(self.failed)}")
        return "; ".join(parts)


def discover(directory: str | Path) -> list[tuple[str, list[Path]]]:
    """Immediate subfolders of ``directory`` with their PDFs (recursive).

    PDFs lying directly in the sync root are ignored (no tag to assign).
    """
    root = Path(directory)
    subjects: list[tuple[str, list[Path]]] = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        pdfs = sorted(p for p in entry.rglob("*.pdf") if p.is_file())
        if pdfs:
            subjects.append((entry.name, pdfs))
    return subjects


def _detect_style(pdf_path: str) -> str:
    """Pick the deterministic style that splits this PDF into more questions."""
    doc = load_pdf(pdf_path)
    counts = {}
    for style in parser.label_styles():
        counts[style] = len(parser.split_questions(doc, style=style))
    return max(counts, key=counts.get)


def _parse_deterministic(pdf_path: str, style: str) -> list[dict]:
    doc = load_pdf(pdf_path)
    questions = parser.split_questions(doc, style=style)
    for q in questions:
        q.body_latex = normalise.to_latex(q.body)
    return [q.to_dict() for q in questions]


def _unique_label(label: str, section: str,
                  seen: set[tuple[str, str]]) -> str:
    """Suffix a repeated label ("X (2)", "X (3)") within one document.

    Keeps the UNIQUE(document_id, section, label) index from rejecting a
    whole document when labels repeat (e.g. an unrecognized solutions
    section reusing the question numbering).
    """
    unique = label
    n = 2
    while (section, unique) in seen:
        unique = f"{label} ({n})"
        n += 1
    seen.add((section, unique))
    return unique


def sync_directory(
    conn: sqlite3.Connection,
    directory: str | Path,
    *,
    model: str = transcribe.DEFAULT_MODEL,
    cap: int = 0,
    key: str | None = None,
    call: transcribe.ModelCall | None = None,
    fresh: bool = False,
    progress: Callable[[dict], None] | None = None,
) -> SyncResult:
    """Run one sync pass over the study directory. Commits per document."""
    root = Path(directory)
    if not root.is_dir():
        raise NotADirectoryError(str(root))
    key = key or transcribe.load_api_key(root)
    if not key:
        raise RuntimeError(
            "no OpenRouter API key: set OPENROUTER_API_KEY in "
            f"{root}/.env"
        )
    result = SyncResult(fresh=fresh)
    if fresh:
        conn.execute("DELETE FROM queue")
        conn.execute("DELETE FROM questions")
        conn.execute("DELETE FROM document_tags")
        conn.execute("DELETE FROM documents")
        conn.commit()

    subjects = discover(root)

    def emit(event: dict) -> None:
        # A failing progress UI must never break the sync pass itself.
        if progress is None:
            return
        try:
            progress(event)
        except Exception:
            pass

    emit({"stage": "start", "total_docs": len(subjects)})

    for doc_index, (tag_name, pdfs) in enumerate(subjects):
        tag_row_id = db.add_tag_or_get(conn, tag_name)
        for pdf in pdfs:
            display = pdf.name
            emit({
                "stage": "document",
                "name": display,
                "tag": tag_name,
                "doc_index": doc_index,
                "total_docs": len(subjects),
            })
            try:
                with open(pdf, "rb") as fh:
                    if fh.read(5) != b"%PDF-":
                        result.failed.append(display)
                        continue
                sha = sha256_file(str(pdf))
                existing = db.document_by_sha(conn, sha)
                if existing is not None:
                    # sha-gate hit: reuse the existing document, but reconcile
                    # tags in case the subject folder was renamed or moved.
                    if tag_name not in db.tags_for_document(conn, existing):
                        db.assign_tag(conn, existing, tag_row_id)
                        conn.commit()
                        result.retagged.append(display)
                    result.skipped.append(display)
                    continue
                style = _detect_style(str(pdf))
                parsed = _parse_deterministic(str(pdf), style)
                document_id = db.insert_document(
                    conn, display, sha, str(pdf.resolve())
                )
                # LLM pass (with per-question crop + deterministic fallback).
                # Crops render here on the main thread (pymupdf is not
                # thread-safe); only the network call runs in the pool.
                ids: list[int] = []
                seen_labels: set[tuple[str, str]] = set()

                def _segments(i: int, q: dict) -> list[tuple[int, str | None]]:
                    """Crop segments: primary region plus continuation pages.

                    The parser's ``end_page`` is the last page that contributed
                    body lines, so pages between questions (figures, blanks)
                    are never included. When the next label exists it bounds
                    the final segment; the last question runs to its page end.
                    """
                    nxt = parsed[i + 1] if i + 1 < len(parsed) else None
                    segments = [
                        (q["page"], q["label"], nxt["label"] if nxt else None)
                    ]
                    end_page = q.get("end_page") or q["page"]
                    for p in range(q["page"] + 1, end_page + 1):
                        segments.append(
                            (p, nxt["label"] if nxt and p == nxt["page"] else None)
                        )
                    return segments

                admitted = [(i, q) for i, q in enumerate(parsed)
                            if cap == 0 or i < cap]
                crops: dict[int, bytes] = {}
                for i, q in admitted:
                    segments = _segments(i, q)
                    crops[i] = transcribe.crop_png_bytes(
                        str(pdf), q["page"], q["label"],
                        segments[0][2],
                        continuation=segments[1:],
                    )

                def _attempt(i: int, crop: bytes):
                    """One LLM transcription; returns (i, outcome) where outcome
                    is ("llm", result dict) or ("fallback", error)."""
                    try:
                        out = transcribe.transcribe_question(
                            parsed[i]["label"], crop, model=model, key=key,
                            call=call,
                        )
                        return i, "llm", out
                    except (transcribe.TranscriptionError, RuntimeError) as err:
                        return i, "fallback", err

                # outcome per question index: None while pending/never run.
                outcomes: dict[int, tuple] = {}
                if admitted:
                    workers = min(10, len(admitted))
                    with ThreadPoolExecutor(max_workers=workers) as pool:
                        futures = {
                            pool.submit(_attempt, i, crops[i]): i
                            for i, _ in admitted
                        }
                        for fut in as_completed(futures):
                            i, kind, payload = fut.result()
                            outcomes[i] = (kind, payload)
                            # Completion order is nondeterministic under the
                            # pool; report the monotonic completed count so
                            # the dashboard never shows the index going back.
                            emit({
                                "stage": "question",
                                "name": display,
                                "question_index": len(outcomes),
                                "question_total": len(parsed),
                            })

                for i, q in enumerate(parsed):
                    engine, confidence, warnings = "pymupdf", None, list(q["warnings"])
                    body, body_latex = q["body"], q["body_latex"]
                    outcome = outcomes.get(i)
                    if outcome is None:
                        warnings.append("llm skipped: cost cap reached")
                        result.uncapped += 1
                    elif outcome[0] == "llm":
                        out = outcome[1]
                        engine, confidence = "llm", out["confidence"]
                        body, body_latex = out["body"], out["body_latex"]
                        result.llm += 1
                    else:
                        warnings.append(
                            f"llm transcription failed: {outcome[1]}"
                        )
                        result.fallback += 1
                    ids.append(
                        db.insert_question(
                            conn, document_id,
                            page_number=q["page"], section=q["section"],
                            label=_unique_label(q["label"], q["section"],
                                                seen_labels),
                            body=body, body_latex=body_latex,
                            hint=q["hint"], warnings=warnings,
                            engine=engine, confidence=confidence,
                            solution=NO_SOLUTION,
                        )
                    )
                db.assign_tag(conn, document_id, tag_row_id)
                db.append_to_queue(conn, ids)
                conn.commit()
                result.questions += len(ids)
                result.imported.append(display)
            except (OSError, ValueError, RuntimeError,
                    pymupdf.FileDataError) as err:
                # unreadable/corrupt PDFs must not kill the pass
                conn.rollback()
                result.failed.append(f"{display}: {err}")
    emit({"stage": "done", "result": result})
    return result
