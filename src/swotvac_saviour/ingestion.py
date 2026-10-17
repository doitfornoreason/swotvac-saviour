"""PDF ingestion: structured text extraction, noise stripping, TOC-based sections."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import pymupdf


@dataclass
class ParsedPage:
    """One cleaned page of a document."""

    number: int  # 1-based page number as printed in the PDF
    lines: list[str] = field(default_factory=list)


@dataclass
class ParsedDocument:
    """Cleaned pages plus the table of contents of a PDF."""

    pages: list[ParsedPage] = field(default_factory=list)
    # (level, title, first_page_1based)
    toc: list[tuple[int, str, int]] = field(default_factory=list)

    def section_for_page(self, page: int) -> str:
        """Return the finest TOC section covering ``page`` (1-based)."""
        best = ""
        for level, title, first_page in self.toc:
            if level > 2:
                continue
            if first_page <= page:
                best = title
        return best


def _line_frequencies(pages: list[list[str]], edges: int) -> dict[str, int]:
    """Count how often lines appear in the top/bottom ``edges`` lines of pages."""
    freq: dict[str, int] = {}
    for lines in pages:
        seen: set[str] = set()
        for line in lines[:edges] + (lines[-edges:] if len(lines) > edges else []):
            key = line.strip()
            if key and key not in seen:
                seen.add(key)
                freq[key] = freq.get(key, 0) + 1
    return freq


def _toc_titles(toc: list[tuple[int, str, int]]) -> set[str]:
    """All TOC title variants that can appear as printed headings."""
    titles: set[str] = set()
    for _, title, _ in toc:
        t = title.strip()
        if not t:
            continue
        titles.add(t)
        # printed chapter headings carry a numeric/letter prefix: "1. Metric ..."
        m = re.match(r"^(?:\d+|[A-Z])\.\s+(.+)$", t)
        if m:
            titles.add(m.group(1).strip())
    return titles


def _strip_noise(
    pages: list[list[str]], toc_titles: set[str] | None = None
) -> list[list[str]]:
    """Remove running headers/footers, bare page numbers and TOC title lines.

    A line is noise when it occurs in the edge zone of at least 3 pages (running
    header/footer) or is a bare page number. TOC titles (section/chapter names)
    are structural metadata and never body content, so they are removed wherever
    they appear - section attribution comes from the TOC instead.
    """
    titles = toc_titles or set()
    freq = _line_frequencies(pages, edges=2)
    cleaned: list[list[str]] = []
    for lines in pages:
        keep: list[str] = []
        n = len(lines)
        for idx, line in enumerate(lines):
            key = line.strip()
            in_edge = idx < 2 or idx >= n - 2
            is_number = bool(key) and key.isdigit()
            if in_edge and (is_number or (key and freq.get(key, 0) >= 3)):
                continue
            if key in titles:
                continue
            keep.append(line)
        cleaned.append(keep)
    return cleaned


def load_pdf(path: str) -> ParsedDocument:
    """Open a PDF and return cleaned pages with its table of contents."""
    doc = pymupdf.open(path)
    try:
        toc = [(level, title, int(page)) for level, title, page in doc.get_toc()]
        raw_pages: list[list[str]] = [page.get_text().splitlines() for page in doc]
    finally:
        doc.close()

    cleaned = _strip_noise(raw_pages, _toc_titles(toc))
    parsed = ParsedDocument(toc=toc)
    for idx, lines in enumerate(cleaned):
        parsed.pages.append(ParsedPage(number=idx + 1, lines=lines))
    return parsed