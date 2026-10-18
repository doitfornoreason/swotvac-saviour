"""Folder sync: subject folders -> tags, PDFs -> LLM-transcribed questions.

Layout: the study directory holds one folder per subject; each subject folder
holds that subject's question PDFs (recursively). The folder name becomes the
tag. Sync is idempotent via sha256: already-imported PDFs are skipped; a fresh
sync wipes documents/questions/state and re-parses everything.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from . import db, importer, normalise, parser, transcribe
from .ingestion import load_pdf

NO_SOLUTION = db.NO_SOLUTION


@dataclass
class SyncResult:
    imported: list[str] = field(default_factory=list)  # display names
    skipped: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)  # unreadable PDFs
    questions: int = 0
    llm: int = 0
    fallback: int = 0
    uncapped: int = 0  # questions kept deterministic due to the cost cap
    fresh: bool = False

    def summary(self) -> str:
        parts = [f"{len(self.imported)} imported, {len(self.skipped)} skipped"]
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
    for style in parser._LABEL_PATTERNS:
        counts[style] = len(parser.split_questions(doc, style=style))
    return max(counts, key=counts.get)


def _parse_deterministic(pdf_path: str, style: str) -> list[dict]:
    doc = load_pdf(pdf_path)
    questions = parser.split_questions(doc, style=style)
    for q in questions:
        q.body_latex = normalise.to_latex(q.body)
    return [q.to_dict() for q in questions]


def sync_directory(
    conn: sqlite3.Connection,
    directory: str | Path,
    *,
    model: str = transcribe.DEFAULT_MODEL,
    cap: int = 0,
    key: str | None = None,
    call: transcribe.ModelCall = transcribe.openrouter_call,
    fresh: bool = False,
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

    for tag_name, pdfs in discover(root):
        tag_row_id = db.add_tag_or_get(conn, tag_name)
        for pdf in pdfs:
            display = pdf.name
            try:
                with open(pdf, "rb") as fh:
                    if fh.read(5) != b"%PDF-":
                        result.failed.append(display)
                        continue
                sha = importer.sha256_file(str(pdf))
                existing = db.document_by_sha(conn, sha)
                if existing is not None:
                    result.skipped.append(display)
                    continue
                style = _detect_style(str(pdf))
                parsed = _parse_deterministic(str(pdf), style)
                document_id = db.insert_document(
                    conn, display, sha, str(pdf.resolve())
                )
                # LLM pass (with per-question crop + deterministic fallback)
                ids: list[int] = []
                for i, q in enumerate(parsed):
                    engine, confidence, warnings = "pymupdf", None, list(q["warnings"])
                    body, body_latex = q["body"], q["body_latex"]
                    if cap == 0 or i < cap:
                        crop = transcribe.crop_png_bytes(
                            str(pdf), q["page"], q["label"],
                            parsed[i + 1]["label"] if i + 1 < len(parsed) else None,
                        )
                        try:
                            out = transcribe.transcribe_question(
                                q["label"], crop, model=model, key=key, call=call,
                            )
                            engine, confidence = "llm", out["confidence"]
                            body, body_latex = out["body"], out["body_latex"]
                            result.llm += 1
                        except (transcribe.TranscriptionError, RuntimeError) as err:
                            warnings.append(f"llm transcription failed: {err}")
                            result.fallback += 1
                    else:
                        warnings.append("llm skipped: cost cap reached")
                        result.uncapped += 1
                    ids.append(
                        db.insert_question(
                            conn, document_id,
                            page_number=q["page"], section=q["section"],
                            label=q["label"], body=body, body_latex=body_latex,
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
            except Exception as err:  # unreadable PDFs must not kill the pass
                conn.rollback()
                result.failed.append(f"{display}: {err}")
    return result
