"""Parser tests measured against the sample documents in samples/."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from swotvac_saviour import normalise, parser
from swotvac_saviour.ingestion import load_pdf

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE = REPO_ROOT / "samples" / "exercises.pdf"
SAMPLE_NUMBERED = REPO_ROOT / "samples" / "MAST20030ProblemBooklet_Topic4.pdf"

# Exercise 1.1. / Exercise A.8. / Question 3. / Q 2.4.
LABEL_RE = re.compile(r"^(?:Exercise (?:\d+\.\d+|[A-Z]\.\d+)|Question \d+(?:\.\d+)?|Q \d+(?:\.\d+)?)\.$")


@pytest.fixture(scope="module")
def questions() -> list[dict]:
    doc = load_pdf(str(SAMPLE))
    qs = parser.split_questions(doc)
    for q in qs:
        q.body_latex = normalise.to_latex(q.body)
    return [q.to_dict() for q in qs]


def test_question_count_and_uniqueness(questions):
    # 91 numeric exercises + 15 appendix A + 16 appendix B
    assert len(questions) == 122
    labels = [q["label"] for q in questions]
    assert len(set(labels)) == 122


def test_labels_match_exercise_pattern(questions):
    for q in questions:
        assert LABEL_RE.match(q["label"]), q["label"]


def test_no_wrapped_cross_references_split(questions):
    # "Tutorial Question 2.7." wraps onto its own line; it must not become a question
    assert "Question 2.7." not in {q["label"] for q in questions}


def test_no_running_headers_in_bodies(questions):
    for q in questions:
        assert "MAST30026 MHS" not in q["body"]


def test_hint_attachment(questions):
    hints = [q for q in questions if q["hint"]]
    assert len(hints) == 14
    by_label = {q["label"]: q for q in questions}
    hint = by_label["Exercise 1.3."]["hint"]
    assert hint and ("Cauchy" in hint or "triangle" in hint)


def test_sub_parts_stay_with_parent(questions):
    by_label = {q["label"]: q for q in questions}
    body = by_label["Exercise 1.10."]["body_latex"]  # normalised: ligatures fixed
    for marker in ["(a) Define", "(b) Extend", "(c) Next define", "(d) Finally define"]:
        assert marker in body, marker


def test_appendix_questions_present(questions):
    labels = {q["label"] for q in questions}
    assert "Exercise A.1." in labels
    assert "Exercise B.16." in labels
    by_label = {q["label"]: q for q in questions}
    assert by_label["Exercise A.1."]["section"] == "Equivalence relations"


def test_sections_assigned_from_toc(questions):
    by_label = {q["label"]: q for q in questions}
    assert by_label["Exercise 1.1."]["section"] == "Metrics"


def test_body_latex_normalisation(questions):
    by_label = {q["label"]: q for q in questions}
    body = by_label["Exercise 1.1."]["body_latex"]
    assert r"\mid d(x,y)" in body
    assert r"\leq d(x,t)" in body
    assert r"\in X" in body
    assert "\ufb01" not in body  # no ligature glyphs survive


def test_no_unknown_control_words():
    """Every control word in body_latex must be a known KaTeX command.

    Glued commands (\inX) or regex backtracking artifacts (\mi d) would show up
    as unknown words.
    """
    word_re = re.compile(r"\\([a-zA-Z]+)")
    for path, style in [
        (SAMPLE, "exercise"),
        (SAMPLE_NUMBERED, "number"),
    ]:
        doc = load_pdf(str(path))
        for q in parser.split_questions(doc, style=style):
            lx = normalise.to_latex(q.body)
            for m in word_re.finditer(lx):
                assert m.group(1) in normalise.KNOWN_CONTROL_WORDS, (q.label, m.group(0))


def test_pages_recorded(questions):
    by_label = {q["label"]: q for q in questions}
    assert by_label["Exercise 1.1."]["page"] == 5


def test_numbered_style_sample():
    doc = load_pdf(str(SAMPLE_NUMBERED))
    qs = parser.split_questions(doc, style="number")
    # 7 problems; pages 3-4 are a Solutions section and are skipped
    assert len(qs) == 7
    assert [q.label for q in qs] == [f"Question {i}." for i in range(1, 8)]
    by_label = {q.label: q for q in qs}
    # sub-parts stay with the parent question
    assert "(a) Sketch" in by_label["Question 4."].body
    assert "(e) Write down" in by_label["Question 4."].body
    # page numbers are stripped from the top of pages
    assert "MAST20030" not in by_label["Question 4."].body
    # solutions boundary recorded as a warning
    assert any("solutions" in w for w in qs[-1].warnings)