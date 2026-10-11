"""Grade vocabulary: what customers write, mapped to the catalog's grades, and the order grades rank in."""

from __future__ import annotations

import re

GRADE_ORDER = (
    "technical",
    "reagent",
    "pharma_usp",
    "pharma_ep",
)  # the default for an unstated grade is the first offered

_ALIASES = {
    "technical": (
        "technical",
        "tech",
        "tg",
        "industrial",
        "commercial",
        "technische qualität",
        "technisch",
        "fibre grade",
    ),
    "reagent": (
        "reagent",
        "ar",
        "analytical",
        "analytical reagent",
        "acs",
        "acs reagent",
        "hplc",
        "lab",
        "laboratory",
        "reagent grade",
        "p.a.",
        "pa",
    ),
    "pharma_usp": ("usp", "usp-nf", "usp/nf", "nf", "pharma usp", "bp/usp", "usp/bp"),
    "pharma_ep": ("ep", "ph. eur.", "ph eur", "pheur", "ph.eur.", "european pharmacopoeia", "pharma ep"),
}
_LOOKUP = {alias: grade for grade, aliases in _ALIASES.items() for alias in aliases}


def canonical_grade(text: str | None) -> str | None:
    """The catalog grade a free-text grade means, or None when it names none of them."""
    if not text:
        return None
    t = re.sub(r"\s+", " ", text.strip().lower()).removesuffix(" grade").strip()
    if t in GRADE_ORDER:
        return t
    if t in _LOOKUP:
        return _LOOKUP[t]
    # a longer text ("USP grade, stabilised", "fibre grade/technical"): every alias found must agree on one grade
    found = {grade for alias, grade in _LOOKUP.items() if re.search(rf"(?<![\w.]){re.escape(alias)}(?![\w])", t)}
    return found.pop() if len(found) == 1 else None


def default_grade(offered: list[str]) -> str:
    return min(offered, key=GRADE_ORDER.index)
