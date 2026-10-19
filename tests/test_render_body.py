"""render_body: body_latex -> safe HTML (lists, text styles, math modes)."""

from __future__ import annotations

from swotvac_saviour import normalise


def test_text_styles_render_as_elements():
    html = normalise.render_body(r"\textbf{prove} and \textit{think} about \emph{this} \texttt{now}")
    assert "<strong>prove</strong>" in html
    assert "<em>think</em>" in html
    assert "<em>this</em>" in html
    assert "<code>now</code>" in html
    assert "\\" not in html


def test_itemize_renders_as_list():
    html = normalise.render_body(
        "Pick one:\n\\begin{itemize}\n\\item first thing $x^2$\n\\item second thing\n\\end{itemize}"
    )
    assert '<ul class="q-list">' in html
    assert html.count("<li>") == 2
    assert "first thing" in html
    assert '<span class="katex-inline">x^2</span>' in html
    assert "\\item" not in html
    assert "\\begin{itemize}" not in html


def test_itemize_with_labelled_items():
    html = normalise.render_body(
        "\\begin{itemize}\n\\item[(a)] $\\int z\\,dz$\n\\item[(b)] plain\n\\end{itemize}"
    )
    assert '<span class="item-tag">(a)</span>' in html
    assert '<span class="item-tag">(b)</span>' in html
    assert "<li>" in html


def test_enumerate_renders_as_ordered_list():
    html = normalise.render_body(
        "\\begin{enumerate}\n\\item one\n\\item two\n\\end{enumerate}"
    )
    assert '<ol class="q-list">' in html


def test_unmatched_begin_runs_to_end():
    # LLM occasionally drops \end: content still renders as a list.
    html = normalise.render_body("intro\n\\begin{itemize}\n\\item a\n\\item b")
    assert "<li>" in html
    assert "b" in html


def test_display_math_block_class():
    html = normalise.render_body("Solve\n$$\\begin{cases} x = 1 \\end{cases}$$\ndone.")
    assert '<span class="katex-display-block">' in html
    assert "cases" in html


def test_math_escaping_is_safe():
    html = normalise.render_body("text with <script>alert(1)</script> and $a<b$ math")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert '<span class="katex-inline">a&lt;b</span>' in html


def test_unclosed_math_runs_to_end():
    html = normalise.render_body("value is $x + y")
    assert '<span class="katex-inline">x + y</span>' in html


def test_plain_prose_stays_escaped_text():
    html = normalise.render_body("plain body with no math at all")
    assert html == "plain body with no math at all"


def test_style_group_escapes_inner_html():
    html = normalise.render_body(r"\textbf{bold <b>inject</b>}")
    assert "<strong>bold &lt;b&gt;inject&lt;/b&gt;</strong>" in html


def test_nested_math_inside_style_group():
    html = normalise.render_body(r"\emph{note that $x \in A$ always}")
    assert "<em>" in html
    assert '<span class="katex-inline">x \\in A</span>' in html
