"""Question-level splitting: labels, hints, sub-parts stay with the parent."""

from __future__ import annotations

from dataclasses import dataclass, field
import re

from .ingestion import ParsedDocument

# A question starts at a line matching one of these label patterns, anchored to
# the start of the line so inline cross-references ("see Exercise 1.3") never
# become split points.
_LABEL_PATTERNS: dict[str, list[tuple[str, re.Pattern[str]]]] = {
    "exercise": [
        ("Exercise", re.compile(r"^Exercise (\d+\.\d+)\.\s*(.*)")),
        ("Exercise", re.compile(r"^Exercise ([A-Z]\.\d+)\.\s*(.*)")),
        ("Question", re.compile(r"^Question (\d+(?:\.\d+)?)\.\s*(.*)")),
        ("Q", re.compile(r"^Q (\d+(?:\.\d+)?)\.\s*(.*)")),
    ],
    # Numbered-list booklets ("4. Consider the function"). The trailing space
    # excludes "2.1." style headings; display math lines like "1," never match.
    "number": [
        ("Question", re.compile(r"^(\d+)\.\s+(.*)")),
    ],
}
# Default kept for backwards compatibility with direct imports.
LABEL_PATTERNS = _LABEL_PATTERNS["exercise"]


def label_styles() -> list[str]:
    """Supported question-label styles (public API for CLI/sync)."""
    return sorted(_LABEL_PATTERNS)

HINT_RE = re.compile(r"\[Hint:(.*?)\]", re.DOTALL)

# A page starting a solutions/answers section ends the question pool: solutions
# are stored separately in a later phase, and their labels repeat the problems'.
SOLUTIONS_BOUNDARY_RE = re.compile(
    r"^\s*[-\u2014=]+\s*(solutions?|answers?)\b"
    r"|^\s*(solutions?|answers?)\s*[-\u2014=]*\s*$"
    r"|^\s*index\s*$",
    re.IGNORECASE,
)

_TERMINAL_PUNCT = tuple(".!?:)]")


@dataclass
class Question:
    """One question with its source attribution and attached hint."""

    label: str
    body: str = ""
    body_latex: str = ""
    page: int = 0
    section: str = ""
    hint: str | None = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "section": self.section,
            "page": self.page,
            "body": self.body,
            "body_latex": self.body_latex,
            "hint": self.hint,
            "warnings": self.warnings,
        }


def split_questions(doc: ParsedDocument, style: str = "exercise") -> list[Question]:
    """Split cleaned page text into questions at line-start labels.

    ``style`` selects the label pattern set: "exercise" for Exercise/Question/Q
    labels, "number" for numbered-list booklets.
    """
    patterns = _LABEL_PATTERNS[style]
    questions: list[Question] = []
    current: Question | None = None
    body_lines: list[str] = []

    def flush() -> None:
        nonlocal current, body_lines
        if current is None:
            return
        body = "\n".join(body_lines).strip()
        current.body = body
        hint_match = HINT_RE.search(body)
        if hint_match:
            current.hint = hint_match.group(1).strip()
            body = HINT_RE.sub("", body).strip()
        if not body:
            current.warnings.append("empty body")
        elif not body.rstrip().endswith((".", "!", "?", ":", ")")):
            current.warnings.append("suspected truncation")
        current.body = body
        current.body_latex = body  # replaced by normalisation step
        questions.append(current)
        current = None
        body_lines = []

    def match(line: str) -> tuple[str, str] | None:
        for prefix, pattern in patterns:
            m = pattern.match(line)
            if m:
                return f"{prefix} {m.group(1)}.", m.group(2)
        return None

    def is_continuation() -> bool:
        """True when a bare label line follows an unfinished sentence.

        Wrapped cross-references ("...variation on Tutorial\\nQuestion 2.7.")
        produce a label line with nothing after the label; real questions with
        an empty rest still follow a completed line (punctuation or blank).
        """
        if current is None:
            return False
        for prev in reversed(body_lines):
            if prev.strip():
                return not prev.rstrip().endswith(_TERMINAL_PUNCT)
        return False

    boundary: tuple[int, str] | None = None  # (page, kind)
    for page in doc.pages:
        if boundary is not None:
            break
        for line in page.lines:
            stripped = line.strip()
            boundary_match = SOLUTIONS_BOUNDARY_RE.match(stripped)
            if boundary_match:
                kind = "index" if boundary_match.group(0).lower().lstrip("-= ").startswith("index") else "solutions"
                boundary = (page.number, kind)
                break
            found = match(stripped) if stripped else None
            if found and not found[1] and is_continuation():
                found = None
            if found:
                flush()
                current = Question(label=found[0], page=page.number)
                current.section = doc.section_for_page(page.number)
                if found[1]:
                    body_lines.append(found[1])
            elif current is not None:
                body_lines.append(stripped if stripped else "")

    flush()
    if boundary is not None and questions:
        page_no, kind = boundary
        questions[-1].warnings.append(
            f"{kind} section starts at page {page_no}; later pages skipped"
        )
    return questions