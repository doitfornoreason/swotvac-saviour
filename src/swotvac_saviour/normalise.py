"""Unicode/LaTeX normalisation: ligature fixes and math to LaTeX commands."""

from __future__ import annotations

import re
from functools import lru_cache
from html import escape as _html_escape

# Ligatures produced by PDF text extraction.
LIGATURES: dict[str, str] = {
    "\ufb00": "ff",
    "\ufb01": "fi",
    "\ufb02": "fl",
    "\ufb03": "ffi",
    "\ufb04": "ffl",
}

# Unicode math operators and letters as extracted from LaTeX-produced PDFs,
# mapped to KaTeX-compatible LaTeX commands. Single codepoints only: this table
# feeds str.translate.
MATH_MAP: dict[str, str] = {
    "\u2264": r"\leq",
    "\u2265": r"\geq",
    "\u226b": r"\gg",
    "\u226a": r"\ll",
    "\u2212": "-",  # unicode minus sign
    "\u2236": r"\colon",
    "\u223c": r"\sim",
    "\u2217": r"\ast",
    "\u2126": r"\Omega",  # OHM SIGN variant of capital omega
    "\u2113": r"\ell",
    "\u22c3": r"\bigcup",
    "\u22c2": r"\bigcap",
    "\u22c1": r"\bigvee",
    "\u22c0": r"\bigwedge",
    "\u03d5": r"\phi",
    "\u03f5": r"\epsilon",
    "\u22ee": r"\vdots",
    "\u2202": r"\partial",
    "\u2a7d": r"\leq",  # \leqslant variant
    "\u2a7e": r"\geq",  # \geqslant variant
    "\u2260": r"\neq",
    "\u2208": r"\in",
    "\u2209": r"\notin",
    "\u2286": r"\subseteq",
    "\u2282": r"\subset",
    "\u228a": r"\subsetneq",
    "\u222a": r"\cup",
    "\u2229": r"\cap",
    "\u2205": r"\emptyset",
    "\u00d7": r"\times",
    "\u00f7": r"\div",
    "\u2192": r"\to",
    "\u2194": r"\leftrightarrow",
    "\u21a6": r"\mapsto",
    "\u21d2": r"\Rightarrow",
    "\u21d4": r"\Leftrightarrow",
    "\u221e": r"\infty",
    "\u2211": r"\sum",
    "\u220f": r"\prod",
    "\u222b": r"\int",
    "\u22c5": r"\cdot",
    "\u2219": r"\cdot",
    "\u221a": r"\sqrt",
    "\u2216": r"\setminus",
    "\u2223": r"\mid",
    "\u2225": r"\|",
    "\u2248": r"\approx",
    "\u2261": r"\equiv",
    "\u221d": r"\propto",
    "\u2200": r"\forall",
    "\u2203": r"\exists",
    "\u00ac": r"\neg",
    "\u2227": r"\wedge",
    "\u2228": r"\vee",
    "\u2295": r"\oplus",
    "\u2297": r"\otimes",
    "\u25cb": "^{\\circ}",  # interior/closure ring
    "\u2218": r"\circ",
    # Big-delimiter glyph pieces from cases/environments: left pieces become
    # "[", right pieces "]", separator bars are dropped. Real structure is
    # recovered later by OCR (Texo/LLM tier); this keeps text readable.
    "\u23a1": "[", "\u23a2": "[", "\u23a3": "[",
    "\u23a4": "]", "\u23a5": "]", "\u23a6": "]",
    "\u23a7": "[", "\u23a8": "[", "\u23a9": "[",
    "\u23ab": "]", "\u23ac": "]", "\u23ad": "]",
    "\u23aa": "",
    "\uf8f1": "]", "\uf8f2": "]", "\uf8f3": "]", "\uf8f4": "]",
    "\u2026": r"\ldots",
    "\u22ef": r"\cdots",
    "\u2032": "'",
    "\u000b": " ",
    "\u000c": "\n",
    "\u00a0": " ",
    "\u2013": "--",
    "\u2014": "---",
    "\u2018": "`",
    "\u2019": "'",
    "\u201c": "``",
    "\u201d": "''",
    # Greek letters
    "\u03b1": r"\alpha",
    "\u03b2": r"\beta",
    "\u03b3": r"\gamma",
    "\u03b4": r"\delta",
    "\u03b5": r"\varepsilon",
    "\u03b6": r"\zeta",
    "\u03b7": r"\eta",
    "\u03b8": r"\theta",
    "\u03b9": r"\iota",
    "\u03ba": r"\kappa",
    "\u03bb": r"\lambda",
    "\u03bc": r"\mu",
    "\u03bd": r"\nu",
    "\u03be": r"\xi",
    "\u03c0": r"\pi",
    "\u03c1": r"\rho",
    "\u03c3": r"\sigma",
    "\u03c4": r"\tau",
    "\u03c5": r"\upsilon",
    "\u03c6": r"\phi",
    "\u03c7": r"\chi",
    "\u03c8": r"\psi",
    "\u03c9": r"\omega",
    "\u0393": r"\Gamma",
    "\u0394": r"\Delta",
    "\u0398": r"\Theta",
    "\u039b": r"\Lambda",
    "\u039e": r"\Xi",
    "\u03a0": r"\Pi",
    "\u03a3": r"\Sigma",
    "\u03a6": r"\Phi",
    "\u03a8": r"\Psi",
    "\u03a9": r"\Omega",
    # Blackboard letters
    "\u2115": r"\mathbb{N}",
    "\u2124": r"\mathbb{Z}",
    "\u211a": r"\mathbb{Q}",
    "\u211d": r"\mathbb{R}",
    "\u2102": r"\mathbb{C}",
    # Mangled mapsto seen in extraction ("D->" glyph) handled separately.
    "\u00d0": r"\mapsto ",  # lone arrow-lost variant
}

_MANGLED_MAPSTO = "\u00d0\u2192"

_MATH_TRANSLATE = str.maketrans(MATH_MAP)
_LIGATURE_TRANSLATE = str.maketrans(LIGATURES)

_LATEX_SPECIALS: tuple[tuple[str, str], ...] = (
    ("\\", r"\textbackslash{}"),
    ("&", r"\&"),
    ("%", r"\%"),
    ("$", r"\$"),
    ("#", r"\#"),
    ("_", r"\_"),
    ("{", r"\{"),
    ("}", r"\}"),
    ("~", r"\textasciitilde{}"),
    ("^", r"\textasciicircum{}"),
)


def fix_ligatures(text: str) -> str:
    """Replace PDF ligature glyphs with their letter sequences."""
    return text.translate(_LIGATURE_TRANSLATE)


def escape_latex(text: str) -> str:
    """Escape LaTeX special characters in plain text."""
    for char, repl in _LATEX_SPECIALS:
        text = text.replace(char, repl)
    return text


_COMBINING_MARKS: tuple[tuple[str, str], ...] = (
    ("\u0302", r"\hat"),
    ("\u0303", r"\tilde"),
    ("\u0304", r"\bar"),
    ("\u0307", r"\dot"),
    ("\u0308", r"\ddot"),
)

# Commands we emit that need a separator when a letter follows directly
# ("\inX" is unparseable). Braced commands (\mathbb{N}) terminate cleanly and
# control symbols (\|) are single-character, so neither list applies.
_SPACE_COMMANDS: frozenset[str] = frozenset(
    v[1:]
    for v in MATH_MAP.values()
    if re.fullmatch(r"\\[a-zA-Z]+", v)
)

# Longest-first alternation so "\infty" wins over "\in" etc.
_SPACER_RE = re.compile(
    r"\\(" + "|".join(sorted(_SPACE_COMMANDS, key=len, reverse=True)) + r")(?=[a-zA-Z])"
)


def normalise_math(text: str) -> str:
    """Map unicode math glyphs to LaTeX command equivalents."""
    # combining marks: "x\u0302" -> \hat{x} (runs before translate; the inserted
    # braces survive because escaping already happened in to_latex)
    for mark, command in _COMBINING_MARKS:
        text = re.sub(r"(.)" + mark, lambda m, c=command: c + "{" + m.group(1) + "}", text)
    text = text.replace(_MANGLED_MAPSTO, r"\mapsto")
    text = text.translate(_MATH_TRANSLATE)
    return _SPACER_RE.sub(r"\\\1 ", text)


KNOWN_CONTROL_WORDS: frozenset[str] = _SPACE_COMMANDS | frozenset(
    {
        "textbackslash",
        "textasciitilde",
        "textasciicircum",
        "hat",
        "tilde",
        "bar",
        "dot",
        "ddot",
        "mathbb",
    }
)


def to_latex(text: str) -> str:
    """Full normalisation: ligatures, special escaping, math mapping.

    Order matters: specials are escaped on the raw text first, so LaTeX
    commands inserted by the math map keep their braces and backslashes.
    """
    text = fix_ligatures(text)
    text = escape_latex(text)
    return normalise_math(text)


# --- KaTeX display wrapping -------------------------------------------------
# body_latex is a whole-question blob of prose and math commands. KaTeX treats
# bare prose as math (italic runes), so prose runs are wrapped in \text{...}.
# Literal braces were escaped by escape_latex, so raw braces only appear inside
# inserted commands and are balanced.

_CTRL_WORD_RE = re.compile(r"\\[a-zA-Z]+")
_CTRL_SYM_RE = re.compile(r"\\[^a-zA-Z]")


def _balanced_end(text: str, i: int) -> int:
    """End index after the braced group starting at i (assumes text[i] == '{')."""
    depth = 0
    j = i
    while j < len(text):
        c = text[j]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return j + 1
        j += 1
    return len(text)


@lru_cache(maxsize=2048)
def latex_segments(body_latex: str) -> list[tuple[str, str]]:
    """Split a body_latex blob into ("prose"|"math"|"display", text) runs.

    The LLM delimits math with $...$ (inline) or $$...$$ (display): prose
    outside delimiters renders as plain HTML, math goes to KaTeX. Blobs
    without any $ (legacy deterministic rows) fall back to the control-word
    heuristic below so they still render their math fragments.
    """
    if "$" not in body_latex:
        return _legacy_segments(body_latex)

    segments: list[tuple[str, str]] = []
    prose: list[str] = []
    i = 0
    n = len(body_latex)

    def flush() -> None:
        if prose:
            segments.append(("prose", "".join(prose)))
            prose.clear()

    while i < n:
        c = body_latex[i]
        if c == "$":
            flush()
            if i + 1 < n and body_latex[i + 1] == "$":
                end = body_latex.find("$$", i + 2)
                if end == -1:
                    segments.append(("display", body_latex[i + 2:]))
                    return segments
                segments.append(("display", body_latex[i + 2:end]))
                i = end + 2
            else:
                end = body_latex.find("$", i + 1)
                if end == -1:
                    segments.append(("math", body_latex[i + 1:]))
                    return segments
                segments.append(("math", body_latex[i + 1:end]))
                i = end + 1
        else:
            prose.append(c)
            i += 1
    flush()
    return segments


def _legacy_segments(body_latex: str) -> list[tuple[str, str]]:
    """Pre-$ heuristic: control words and ^{...} superscripts are math, rest prose."""
    segments: list[tuple[str, str]] = []
    prose: list[str] = []
    i = 0
    n = len(body_latex)

    def flush() -> None:
        if prose:
            segments.append(("prose", "".join(prose)))
            prose.clear()

    while i < n:
        c = body_latex[i]
        if c == "\\":
            flush()
            m = _CTRL_WORD_RE.match(body_latex, i) or _CTRL_SYM_RE.match(body_latex, i)
            assert m is not None
            token = m.group(0)
            i = m.end()
            while i < n and body_latex[i] == "{":
                end = _balanced_end(body_latex, i)
                token += body_latex[i:end]
                i = end
            segments.append(("math", token.replace(r"\_", "_")))
        elif c == "^" and i + 1 < n and body_latex[i + 1] == "{":
            flush()
            end = _balanced_end(body_latex, i + 1)
            segments.append(("math", body_latex[i:end]))
            i = end
        else:
            prose.append(c)
            i += 1
    flush()
    return segments




# --- body rendering: body_latex -> safe HTML ---------------------------------
#
# The stored body_latex mixes prose, $...$/$$...$$ math, text-style commands
# (\textbf, \textit, \emph, ...) and list environments (\begin{itemize}).
# latex_segments only understands $-delimiters, so lists and text styles fell
# through as raw text. render_body produces finished HTML instead: everything
# it does not understand stays escaped, everything it emits is escaped itself.

_LIST_ENVS = {"itemize": "ul", "enumerate": "ol"}

# Text-style commands -> HTML elements. KaTeX has no notion of document-level
# text styling, so these only make sense in prose runs.
_STYLE_CMDS = {
    "textbf": "strong",
    "textit": "em",
    "emph": "em",
    "texttt": "code",
    "underline": "u",
}

_ITEM_RE = re.compile(r"\\item(?:\[([^\]]*)\])?")


def _style_group_html(cmd: str, body_latex: str, open_brace: int) -> tuple[str, int]:
    """HTML for one \textbf{...}-style group; returns (html, next_index)."""
    end = _balanced_end(body_latex, open_brace)
    inner = _inline_html(body_latex[open_brace + 1:end - 1])
    tag = _STYLE_CMDS[cmd]
    return f"<{tag}>{inner}</{tag}>", end


def _list_html(env: str, body_latex: str, begin_end: int) -> tuple[str, int]:
    """HTML for one itemize/enumerate environment; returns (html, next_index).

    The environment runs to the matching \\end (depth-counted for nesting) or,
    if unmatched, to the end of the text. Items may contain $math$, text
    styles and nested lists.
    """
    depth = 1
    j = begin_end
    begin_re = re.compile(r"\\begin\{((?:itemize|enumerate))\}")
    end_re = re.compile(r"\\end\{((?:itemize|enumerate))\}")
    while j < len(body_latex):
        m = begin_re.search(body_latex, j)
        e = end_re.search(body_latex, j)
        if e is not None and (m is None or e.start() < m.start()):
            if e.group(1) == env:
                depth -= 1
                if depth == 0:
                    content = body_latex[begin_end:e.start()]
                    j = e.end()
                    break
                j = e.end()
            else:
                j = e.end()
            continue
        if m is not None:
            if m.group(1) == env:
                depth += 1
            j = m.end()
            continue
        j = len(body_latex)
    else:
        content = body_latex[begin_end:]
        j = len(body_latex)

    matches = list(_ITEM_RE.finditer(content))
    if not matches:
        rendered = [f"<li>{_inline_html(content.strip())}</li>"]
    else:
        rendered = []
        for k, m in enumerate(matches):
            start = m.end()
            stop = matches[k + 1].start() if k + 1 < len(matches) else len(content)
            body = _inline_html(content[start:stop])
            if m.group(1):
                body = f'<span class="item-tag">{_inline_html(m.group(1))}</span> {body}'
            rendered.append(f"<li>{body}</li>")
    return f"<{_LIST_ENVS[env]} class=\"q-list\">{''.join(rendered)}</{_LIST_ENVS[env]}>", j


def _math_html(tex: str, display: bool) -> str:
    cls = "katex-display-block" if display else "katex-inline"
    return f'<span class="{cls}">{_html_escape(tex, quote=False)}</span>'


def _escape_prose(text: str) -> str:
    """Escape prose for HTML and turn source newlines into line breaks.

    Sub-parts in the source sit on their own lines; HTML would collapse them
    into one run-on paragraph without this.
    """
    return _html_escape(text, quote=False).replace("\n", "<br>")


def _consume_braced(text: str, i: int) -> int:
    """Index just past a balanced braced group at i (or i if no brace)."""
    if i < len(text) and text[i] == "{":
        return _balanced_end(text, i)
    return i


def _inline_html(body_latex: str) -> str:
    """Render one run: prose (escaped, styles applied), math spans, lists.

    Prose-level commands the list/style handling does not claim are wrapped
    as KaTeX math ("\\quad" between subparts) or, when structurally junk
    (stray \\item, unmatched begin/end), dropped or turned into a break.
    """
    parts: list[str] = []
    prose: list[str] = []
    i = 0
    n = len(body_latex)

    def flush() -> None:
        if prose:
            parts.append(_escape_prose("".join(prose)))
            prose.clear()

    while i < n:
        c = body_latex[i]
        if c == "$":
            flush()
            display = body_latex.startswith("$$", i)
            closer = "$$" if display else "$"
            end = body_latex.find(closer, i + 2 if display else i + 1)
            if end == -1:
                parts.append(_math_html(body_latex[i + len(closer):], display))
                return "".join(parts)
            parts.append(_math_html(body_latex[i + len(closer):end], display))
            i = end + len(closer)
            continue
        if c != "\\":
            prose.append(c)
            i += 1
            continue

        # backslash token: dispatch by kind
        begin_list = re.match(r"\\begin\{(itemize|enumerate)\}", body_latex[i:])
        if begin_list:
            flush()
            html, i = _list_html(begin_list.group(1), body_latex, i + begin_list.end())
            parts.append(html)
            continue
        style = re.match(r"\\(%s)\{" % "|".join(_STYLE_CMDS), body_latex[i:])
        if style:
            flush()
            html, i = _style_group_html(style.group(1), body_latex, i + style.end() - 1)
            parts.append(html)
            continue
        item = re.match(r"\\item(?:\[([^\]]*)\])?", body_latex[i:])
        if item:
            # stray \item outside a list environment: break the line
            flush()
            parts.append("<br>")
            i += item.end()
            continue
        env = re.match(r"\\(?:begin|end)\{([a-zA-Z*]+)\}", body_latex[i:])
        if env:
            # unknown/unmatched environment wrapper: drop it, keep content
            i += env.end()
            continue
        cmd = re.match(r"\\[a-zA-Z]+", body_latex[i:]) or re.match(r"\\[^a-zA-Z]", body_latex[i:])
        assert cmd is not None
        flush()
        end = _consume_braced(body_latex, i + cmd.end())
        parts.append(_math_html(body_latex[i:end], display=False))
        i = end
    flush()
    return "".join(parts)


@lru_cache(maxsize=2048)
def render_body(body_latex: str) -> str:
    """Full safe-HTML rendering of one question body (see module notes above)."""
    if "$" not in body_latex and "\\" not in body_latex:
        return _escape_prose(body_latex)
    return _inline_html(body_latex)
