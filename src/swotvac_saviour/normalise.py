"""Unicode/LaTeX normalisation: ligature fixes and math to LaTeX commands."""

from __future__ import annotations

import re
from functools import lru_cache

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
    """Split a body_latex blob into ("prose"|"math", text) runs.

    Prose renders as plain HTML (wraps naturally, never overflows), math as
    individual KaTeX spans. Escaped underscores (\\_) in math runs are restored
    so KaTeX renders real subscripts.
    """
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


