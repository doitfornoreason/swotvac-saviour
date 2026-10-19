"""Parser tests against synthetic in-memory documents.

ParsedDocument is plain data, so no fixture PDFs are needed: each test builds
pages by hand and asserts the splitting invariants the parser guarantees.
"""

from __future__ import annotations

import re

import pytest

from swotvac_saviour import normalise, parser
from swotvac_saviour.ingestion import ParsedDocument, ParsedPage, load_pdf

# Exercise 1.1. / Exercise A.8. / Question 3. / Q 2.4.
LABEL_RE = re.compile(r"^(?:Exercise (?:\d+\.\d+|[A-Z]\.\d+)|Question \d+(?:\.\d+)?|Q \d+(?:\.\d+)?)\.$")

def make_doc(pages: list[list[str]], toc: list[tuple[int, str, int]] | None = None) -> ParsedDocument:
    """Build a ParsedDocument as load_pdf would after noise stripping."""
    return ParsedDocument(
        pages=[ParsedPage(number=i + 1, lines=lines) for i, lines in enumerate(pages)],
        toc=toc or [],
    )


EXERCISE_PAGES = [
    ["Exercise 1.1. Let (X,d) be a metric space. Show that d(x,y) = 0 implies x = y.",
     "[Hint: use the triangle inequality.]",
     "Exercise 1.2. Prove that the real line is complete."],
    ["Exercise 1.3. Consider the function f(x) = x^2, which generalises the",
     "Question 2.7.",
     "Exercise 1.4. Conclude the proof."],
    ["Exercise A.1. Let V be a vector space.",
     "Exercise A.2. Show that every norm induces a metric."],
]
EXERCISE_TOC = [(1, "Metrics", 1), (1, "Norms", 3)]


@pytest.fixture(scope="module")
def questions() -> list[dict]:
    doc = make_doc(EXERCISE_PAGES, EXERCISE_TOC)
    qs = parser.split_questions(doc)
    for q in qs:
        q.body_latex = normalise.to_latex(q.body)
    return [q.to_dict() for q in qs]


def test_question_count_and_uniqueness(questions):
    assert len(questions) == 6
    labels = [q["label"] for q in questions]
    assert len(set(labels)) == 6


def test_labels_match_exercise_pattern(questions):
    for q in questions:
        assert LABEL_RE.match(q["label"]), q["label"]


def test_no_wrapped_cross_references_split(questions):
    # "Question 2.7." wraps onto its own line after an unfinished sentence;
    # it must not become a question label.
    assert "Question 2.7." not in {q["label"] for q in questions}
    by_label = {q["label"]: q for q in questions}
    assert "Question 2.7." in by_label["Exercise 1.3."]["body"]


def test_hint_attachment(questions):
    hints = [q for q in questions if q["hint"]]
    assert len(hints) == 1
    by_label = {q["label"]: q for q in questions}
    hint = by_label["Exercise 1.1."]["hint"]
    assert hint == "use the triangle inequality."
    assert "[Hint:" not in by_label["Exercise 1.1."]["body"]


def test_sub_parts_stay_with_parent():
    doc = make_doc([[
        "Exercise 1.10. Work with f: R -> R.",
        "(a) Define the derivative.",
        "(b) Extend to higher dimensions.",
        "(c) Next define the Hessian.",
        "(d) Finally define convexity.",
    ]])
    qs = parser.split_questions(doc)
    assert len(qs) == 1
    assert qs[0].label == "Exercise 1.10."
    for marker in ["(a) Define", "(b) Extend", "(c) Next define", "(d) Finally define"]:
        assert marker in qs[0].body


def test_appendix_questions_present(questions):
    labels = {q["label"] for q in questions}
    assert "Exercise A.1." in labels
    assert "Exercise A.2." in labels


def test_sections_assigned_from_toc(questions):
    by_label = {q["label"]: q for q in questions}
    assert by_label["Exercise 1.1."]["section"] == "Metrics"
    assert by_label["Exercise A.1."]["section"] == "Norms"


def test_pages_recorded(questions):
    by_label = {q["label"]: q for q in questions}
    assert by_label["Exercise 1.1."]["page"] == 1
    assert by_label["Exercise A.1."]["page"] == 3
    assert by_label["Exercise A.2."]["end_page"] == 3


def test_summary_page_skipped():
    doc = make_doc([[
        "Exercise 1.1. First question on the pool page.",
    ], [
        "Summary of inner product spaces",
        "Exercise 9.9. This line sits on the summary page and is skipped.",
    ], [
        "Exercise 1.2. The pool continues after the summary page.",
    ]])
    qs = parser.split_questions(doc)
    assert [q.label for q in qs] == ["Exercise 1.1.", "Exercise 1.2."]


def test_no_unknown_control_words():
    """Every control word in body_latex must be a known KaTeX command.

    Glued commands (\inX) or regex backtracking artifacts (\mi d) would show up
    as unknown words.
    """
    word_re = re.compile(r"\\([a-zA-Z]+)")
    for style, pages in [
        ("exercise", EXERCISE_PAGES),
        ("number", [["1. Consider the sequence a_n = 1/n. Show it converges."]]),
    ]:
        doc = make_doc(pages)
        for q in parser.split_questions(doc, style=style):
            lx = normalise.to_latex(q.body)
            for m in word_re.finditer(lx):
                assert m.group(1) in normalise.KNOWN_CONTROL_WORDS, (q.label, m.group(0))


NUMBERED_PAGES = [
    ["1. Consider the sequence a_n = 1/n. Show it converges.",
     "2. Let f be continuous on [0,1]."],
    ["3. Sketch the unit ball in R^2.",
     "(a) Sketch the l1 ball.",
     "(b) Sketch the l2 ball.",
     "4. Show the norm is continuous."],
    ["Solutions",
     "1. The sequence converges to zero.",
     "2. Continuity follows from compactness."],
]


def test_numbered_style_sample():
    doc = make_doc(NUMBERED_PAGES)
    qs = parser.split_questions(doc, style="number")
    # Solutions page terminates the pool: 4 problems, not 6.
    assert [q.label for q in qs] == [f"Question {i}." for i in range(1, 5)]
    by_label = {q.label: q for q in qs}
    # sub-parts stay with the parent question
    assert "(a) Sketch the l1 ball." in by_label["Question 3."].body
    assert "(b) Sketch the l2 ball." in by_label["Question 3."].body


def test_solutions_boundary_warning():
    doc = make_doc(NUMBERED_PAGES)
    qs = parser.split_questions(doc, style="number")
    assert any("solutions" in w for w in qs[-1].warnings), qs[-1].warnings
    assert qs[-1].label == "Question 4."
    assert qs[-1].end_page == 2


def test_suspected_truncation_warning():
    doc = make_doc([["Exercise 1.1. A question with no terminal punctuation"]])
    (q,) = parser.split_questions(doc)
    assert "suspected truncation" in q.warnings


def test_inline_cross_reference_not_split():
    # "see Exercise 1.3" mid-line must never become a split point.
    doc = make_doc([[
        "Exercise 1.1. Use the result of Exercise 1.3 stated earlier.",
        "Exercise 1.2. Conclude the argument.",
    ]])
    assert [q.label for q in parser.split_questions(doc)] == [
        "Exercise 1.1.", "Exercise 1.2.",
    ]


def _pdf_with(path, pages_text, toc):
    import pymupdf

    doc = pymupdf.open()
    for text in pages_text:
        page = doc.new_page()
        page.insert_text((72, 72), text)
    doc.set_toc(toc)
    doc.save(path)
    doc.close()


def test_load_pdf_extracts_toc_and_pages(tmp_path):
    """load_pdf reads text, page numbers and TOC from a generated PDF."""
    path = tmp_path / "doc.pdf"
    _pdf_with(
        path,
        ["Metrics intro", "More metrics", "Norms begin"],
        [[1, "Metrics", 1], [1, "Norms", 3]],
    )
    parsed = load_pdf(str(path))
    assert [p.number for p in parsed.pages] == [1, 2, 3]
    assert parsed.pages[0].lines == ["Metrics intro"]
    assert parsed.section_for_page(1) == "Metrics"
    assert parsed.section_for_page(3) == "Norms"


def test_load_pdf_strips_running_headers(tmp_path):
    """A header line in the edge zone of 3+ pages is stripped everywhere."""
    path = tmp_path / "doc.pdf"
    _pdf_with(
        path,
        [
            "Running Header\nbody one\n1",
            "Running Header\nbody two\n2",
            "Running Header\nbody three\n3",
        ],
        [],
    )
    parsed = load_pdf(str(path))
    for page in parsed.pages:
        assert "Running Header" not in page.lines
        assert not any(line.strip().isdigit() for line in page.lines)
    assert parsed.pages[0].lines == ["body one"]
    assert parsed.pages[2].lines == ["body three"]
