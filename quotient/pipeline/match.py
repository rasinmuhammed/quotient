"""Stage 3, match: which catalog SKU each line means, and which customer sent the RFQ.

Deterministic first, in this order: an exact CAS number (check digit validated), a SKU code, the synonym table
(names, abbreviations, trade names), then a fuzzy name match that only accepts typos. Whatever is left goes to
the model in one batched call, which names the substance it reads and may only pick one of the top five
candidates or say "none". A pick outside the candidates, or one that is not the substance the model itself
named, is discarded and the line held. Every match records how it was made.

Fuzzy matching is deliberately narrow: in chemistry a small edit is often a different substance (diethylene
vs ethylene glycol), so a fuzzy hit where one name contains the other is never accepted without the model.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from quotient.catalog import Catalog
from quotient.domain import Flag, FlagCode, Product
from quotient.grades import GRADE_ORDER, canonical_grade, default_grade
from quotient.llm import LLM, LLMCall
from quotient.pipeline.prompts import system_prompt
from quotient.pipeline.types import Candidate, CustomerMatch, ExtractedLine, Extraction, Match, RFQDocument

MATCH_RULE = "MATCH-v1"
GRADE_RULE = "GRADE-DEFAULT-v1"
FUZZY_MIN, FUZZY_MARGIN, TOP_K = 90.0, 8.0, 5
LEGAL_SUFFIXES = {"inc", "llc", "ltd", "ltda", "gmbh", "bv", "fze", "co", "kk", "sa", "ag", "nv", "pty", "plc", "corp",
                  "company", "limited", "srl", "spa"}


def key(text: str) -> str:
    """Lowercase ASCII words: 'Glycerin 99.7% USP' -> 'glycerin 99 7 usp'."""
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


# ------------------------------------------------------------------------------------------------ CAS numbers

_CAS_DASHED = re.compile(r"(?<!\d)(\d{2,7})[-\s]{1,2}(\d{2})[-\s]{1,2}(\d)(?!\d)")
_CAS_BARE = re.compile(r"(?<![\d.,])(\d{5,10})(?![\d.,])")


def cas_valid(cas: str) -> bool:
    a, b, c = cas.split("-")
    return sum((i + 1) * int(d) for i, d in enumerate(reversed(a + b))) % 10 == int(c)


def find_cas(text: str | None) -> list[str]:
    """CAS numbers in a text, written with dashes, spaces or as a bare number; only those whose check digit holds."""
    if not text:
        return []
    found = [f"{a}-{b}-{c}" for a, b, c in _CAS_DASHED.findall(text)]
    found += [f"{n[:-3]}-{n[-3:-1]}-{n[-1]}" for n in _CAS_BARE.findall(text)]
    return list(dict.fromkeys(c for c in found if c[0] != "0" and cas_valid(c)))


# ------------------------------------------------------------------------------------------------ names


@dataclass(frozen=True)
class NameIndex:
    names: dict[str, str]  # name key -> chemical code
    chem_name: dict[str, str]  # chemical code -> display name
    by_cas: dict[str, str]
    skus: dict[str, list[Product]] = field(default_factory=dict)  # chemical code -> its SKUs

    @classmethod
    def build(cls, catalog: Catalog) -> NameIndex:
        names, chem_name, by_cas, skus = {}, {}, {}, {}
        for p in catalog.products.values():
            chem_name[p.chemical_code] = p.chemical_name
            by_cas[p.cas] = p.chemical_code
            skus.setdefault(p.chemical_code, []).append(p)
            for n in (p.chemical_name, *p.synonyms, *p.trade_names):
                names[key(n)] = p.chemical_code
        return cls(names, chem_name, by_cas, skus)

    def contained(self, text_key: str) -> list[tuple[str, str]]:
        """Catalog names that occur in the text as whole words, longest first."""
        hits = [(n, c) for n, c in self.names.items() if re.search(rf"(?<![a-z0-9]){re.escape(n)}(?![a-z0-9])", text_key)]
        return sorted(hits, key=lambda h: -len(h[0]))


def _by_name(texts: list[tuple[str, str]], idx: NameIndex) -> tuple[str, str, str] | list[str] | None:
    """(code, via, matched name) for an exact or whole-word synonym hit; a list of codes when the longest hits
    disagree; None when nothing is found."""
    for via, text in texts:
        k = key(text)
        if k in idx.names:
            return idx.names[k], via, text
        hits = idx.contained(k)
        if hits:
            longest = len(hits[0][0])
            codes = list(dict.fromkeys(c for n, c in hits if len(n) == longest))
            if len(codes) == 1:
                return codes[0], via, hits[0][0]
            return codes
    return None


def candidates(texts: list[str], idx: NameIndex, k: int = TOP_K) -> list[Candidate]:
    best: dict[str, float] = {}
    for text in texts:
        tk = key(text)
        if not tk:
            continue
        for name, code in idx.names.items():
            s = fuzz.WRatio(tk, name)
            if s > best.get(code, -1):
                best[code] = s
    top = sorted(best.items(), key=lambda kv: (-kv[1], kv[0]))[:k]
    return [Candidate(chemical_code=c, name=idx.chem_name[c], score=round(s, 1)) for c, s in top]


def _fuzzy(texts: list[tuple[str, str]], idx: NameIndex) -> tuple[str, str, str, float] | None:
    """A typo-level match: high similarity, a clear margin over the next chemical, and not a name inside another."""
    scored = []
    for via, text in texts:
        tk = key(text)
        for name, code in idx.names.items():
            if len(name) >= 5:
                scored.append((fuzz.ratio(tk, name), code, via, name, tk))
    if not scored:
        return None
    scored.sort(key=lambda s: -s[0])
    s, code, via, name, tk = scored[0]
    runner_up = next((x[0] for x in scored if x[1] != code), 0.0)
    if s >= FUZZY_MIN and s - runner_up >= FUZZY_MARGIN and name not in tk and tk not in name:
        return code, via, name, s
    return None


# ------------------------------------------------------------------------------------------------ grades


def _pharma_only(text: str | None, offered: list[str]) -> str | None:
    pharma = [g for g in offered if g.startswith("pharma")]
    return pharma[0] if text and re.search(r"\bpharma", text.lower()) and len(pharma) == 1 else None


def resolve_sku(code: str, line: ExtractedLine, document_grade: str | None, idx: NameIndex) -> tuple[Product | None, str, str, list[Flag]]:
    """The SKU of a matched chemical: the grade written on the line, else in the mention, else stated for the whole
    document; the only grade offered; or the lowest grade offered, flagged."""
    skus = sorted(idx.skus[code], key=lambda p: GRADE_ORDER.index(p.grade))
    offered = [p.grade for p in skus]
    name = idx.chem_name[code]
    grade_text = line.grade.value
    stated = (canonical_grade(grade_text) or _pharma_only(grade_text, offered)
              or canonical_grade(line.mention.value) or _pharma_only(line.mention.value, offered))
    source = "line"
    if stated is None and document_grade:
        stated, source = canonical_grade(document_grade), "document"
    if stated is not None:
        sku = next((p for p in skus if p.grade == stated), None)
        if sku is None:
            return None, stated, source, [Flag.of(FlagCode.NO_MATCH, f"{name} is not offered in {stated} grade "
                                                  f"(offered: {', '.join(offered)})", GRADE_RULE)]
        return sku, stated, source, []
    if len(skus) == 1:
        return skus[0], skus[0].grade, "only_grade", []
    chosen = next(p for p in skus if p.grade == default_grade(offered))
    said = f"'{grade_text}' is not a grade we recognise" if grade_text else "no grade stated"
    return chosen, chosen.grade, "default", [Flag.of(
        FlagCode.GRADE_NOT_SPECIFIED, f"{said}; {name} comes in {', '.join(offered)}; priced as {chosen.grade}", GRADE_RULE)]


# ------------------------------------------------------------------------------------------------ products


@dataclass
class _Pending:
    line_no: int
    line: ExtractedLine
    candidates: list[Candidate]


def _decide(line: ExtractedLine, idx: NameIndex) -> Match | _Pending | None:
    texts = [("mention", line.mention.value or "")]
    if line.english_name:
        texts.append(("english_name", line.english_name))

    # a CAS number counts only when it is in the document: a model can recall one for the wrong chemical
    for via, text in (("cas", line.cas.value if line.cas.verified else None), ("mention", line.mention.value)):
        for cas in find_cas(text):
            if cas in idx.by_cas:
                return Match(chemical_code=idx.by_cas[cas], sku=None, method="cas", matched_on=cas, score=100.0,
                             explanation=f"CAS {cas} ({via.replace('_', ' ')})")
    sku_code = re.search(r"\bQX-[A-Z0-9]+-[A-Z]+\b", line.mention.value or "")
    if sku_code:
        code = next((c for c, ps in idx.skus.items() if any(p.sku == sku_code.group(0) for p in ps)), None)
        if code:
            return Match(chemical_code=code, sku=sku_code.group(0), method="sku", matched_on=sku_code.group(0), score=100.0,
                         explanation=f"SKU code {sku_code.group(0)}")
    hit = _by_name(texts, idx)
    if isinstance(hit, tuple):
        code, via, name = hit
        if via == "english_name":
            return Match(chemical_code=code, sku=None, method="translation", matched_on=name, score=100.0,
                         explanation=f"read as '{line.english_name}'; '{name}' is in the synonym table")
        return Match(chemical_code=code, sku=None, method="synonym", matched_on=name, score=100.0,
                     explanation=f"'{name}' is in the synonym table")
    fz = _fuzzy(texts, idx)
    if fz:
        code, via, name, s = fz
        return Match(chemical_code=code, sku=None, method="fuzzy", matched_on=name, score=round(s, 1),
                     explanation=f"spelling close to '{name}' ({s:.0f}/100)")
    cands = candidates([t for _, t in texts], idx)
    if isinstance(hit, list):  # two different chemicals' names of equal length: let the model choose between them
        head = [c for c in cands if c.chemical_code in hit]
        cands = head + [c for c in cands if c.chemical_code not in hit]
    return _Pending(0, line, cands[:TOP_K])


MATCH_SCHEMA_ITEM = {"type": "object", "additionalProperties": False, "required": ["line", "substance", "choice", "reason"],
                     "properties": {"line": {"type": "integer"}, "substance": {"type": "string"}, "choice": {"type": "string"},
                                    "reason": {"type": "string"}}}


@dataclass(frozen=True)
class _Answer:
    choice: str | None
    substance: str
    reason: str


def names_agree(substance: str, code: str, idx: NameIndex) -> bool:
    """Whether the substance the model named is the chemical it picked: one of that chemical's names, a whole-word
    part of one ("acetic acid" in "glacial acetic acid"), or a typo of one. A catalog name inside the model's name
    does not count, as a part or as a typo: "ethylene glycol" is inside "diethylene glycol"."""
    k = key(substance)
    if not k:
        return False
    return any(k == n or re.search(rf"(?<![a-z0-9]){re.escape(k)}(?![a-z0-9])", n)
               or (fuzz.ratio(k, n) >= FUZZY_MIN and n not in k and k not in n)
               for n, c in idx.names.items() if c == code)


def _ask_model(pending: list[_Pending], idx: NameIndex, catalog: Catalog, llm: LLM) -> tuple[dict[int, _Answer], LLMCall]:
    codes = sorted({c.chemical_code for p in pending for c in p.candidates})
    item = {**MATCH_SCHEMA_ITEM, "properties": {**MATCH_SCHEMA_ITEM["properties"], "choice": {"type": "string", "enum": [*codes, "none"]}}}
    schema = {"type": "object", "additionalProperties": False, "required": ["decisions"],
              "properties": {"decisions": {"type": "array", "items": item}}}
    blocks = []
    for p in pending:
        ln = p.line
        cands = "\n".join(f"  - {c.chemical_code} | {c.name} | {idx.skus[c.chemical_code][0].cas}" for c in p.candidates)
        blocks.append(f"Line {p.line_no}: wording '{ln.mention.value}' | English name: {ln.english_name or '-'} | "
                      f"CAS: {ln.cas.value or '-'} | grade: {ln.grade.value or '-'}\nCandidates:\n{cands}")
    user = "TASK: MATCH\n\n" + "\n\n".join(blocks)
    raw, call = llm.json(name="catalog_match", schema=schema, system=system_prompt(catalog), user=user)
    out: dict[int, _Answer] = {}
    for d in raw.get("decisions", []):
        out[int(d["line"])] = _Answer(None if d["choice"] == "none" else d["choice"], d.get("substance", ""), d.get("reason", ""))
    return out, call


def match_lines(extraction: Extraction, catalog: Catalog, llm: LLM | None) -> tuple[list[Match], list[list[Flag]], list[LLMCall]]:
    idx = NameIndex.build(catalog)
    doc_grade = extraction.document_grade.value if extraction.document_grade.verified else None
    decided: list[Match | _Pending | None] = []
    for i, line in enumerate(extraction.lines, start=1):
        d = _decide(line, idx)
        if isinstance(d, _Pending):
            d.line_no = i
        decided.append(d)

    calls: list[LLMCall] = []
    answers: dict[int, _Answer] = {}
    pending = [d for d in decided if isinstance(d, _Pending)]
    if pending and llm is not None:
        answers, call = _ask_model(pending, idx, catalog, llm)
        calls.append(call)

    matches, flags = [], []
    for i, (line, d) in enumerate(zip(extraction.lines, decided, strict=True), start=1):
        line_flags: list[Flag] = []
        if isinstance(d, _Pending):
            allowed = {c.chemical_code for c in d.candidates}
            a = answers.get(i, _Answer(None, "", "no model available" if llm is None else "the model gave no answer"))
            choice, reason = a.choice, a.reason
            if choice is not None and choice not in allowed:
                line_flags.append(Flag.of(FlagCode.AMBIGUOUS_MATCH, f"the model chose {choice}, which was not a candidate; "
                                          "discarded", MATCH_RULE))
                choice = None
            elif choice is not None and not names_agree(a.substance, choice, idx):
                line_flags.append(Flag.of(FlagCode.AMBIGUOUS_MATCH, f"the model read '{line.mention.value}' as "
                                          f"'{a.substance}' but chose {idx.chem_name[choice]}; discarded", MATCH_RULE))
                choice = None
            if choice is None:
                m = Match(chemical_code=None, sku=None, method="none", candidates=tuple(d.candidates),
                          explanation=reason or "no catalog product matches")
                if not line_flags:
                    line_flags.append(Flag.of(FlagCode.NO_MATCH, f"'{line.mention.value}': {m.explanation}", MATCH_RULE))
                matches.append(m)
                flags.append(line_flags)
                continue
            d = Match(chemical_code=choice, sku=None, method="llm", matched_on=line.mention.value,
                      score=next(c.score for c in d.candidates if c.chemical_code == choice), candidates=tuple(d.candidates),
                      explanation=f"model chose among {len(d.candidates)} candidates: {reason}")
        if d.sku:  # a SKU code names its grade itself
            product, grade, source, grade_flags = catalog.product(d.sku), catalog.product(d.sku).grade, "line", []
        else:
            product, grade, source, grade_flags = resolve_sku(d.chemical_code, line, doc_grade, idx)
        line_flags += grade_flags
        matches.append(d.model_copy(update={"sku": product.sku if product else None, "grade": grade, "grade_source": source}))
        flags.append(line_flags)
    return matches, flags, calls


# ------------------------------------------------------------------------------------------------ customer


def _company_key(name: str) -> str:
    return " ".join(w for w in key(name).split() if w not in LEGAL_SUFFIXES)


def match_customer(doc: RFQDocument, extraction: Extraction | None, catalog: Catalog) -> CustomerMatch:
    """The sender's email domain first; then the buying company's name, which must clearly beat every other customer."""
    domains: dict[str, list[str]] = {}
    for c in catalog.customers.values():
        domains.setdefault(c.contact_email.split("@")[1].lower(), []).append(c.customer_id)
    emails = [doc.sender_email] if doc.sender_email else []
    emails += re.findall(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", doc.text)
    for e in emails:
        ids = domains.get(e.split("@")[1].lower(), [])
        if len(ids) == 1:
            c = catalog.customers[ids[0]]
            return CustomerMatch(customer_id=c.customer_id, name=c.name, method="email_domain", matched_on=e, score=100.0)

    names = []
    if extraction and extraction.buyer_company.value:
        names.append(extraction.buyer_company.value)
    if doc.sender_name:
        names.append(doc.sender_name)
    for text in names:
        tk = _company_key(text)
        if not tk:
            continue
        scored = sorted(((fuzz.token_sort_ratio(tk, _company_key(c.name)), c) for c in catalog.customers.values()),
                        key=lambda s: -s[0])
        (best, c), runner_up = scored[0], scored[1][0]
        if best >= 90 and best - runner_up >= 8:
            return CustomerMatch(customer_id=c.customer_id, name=c.name, method="company_name", matched_on=text, score=best)
    doc_key = key(doc.text)
    present = [c for c in catalog.customers.values() if _company_key(c.name) and f" {_company_key(c.name)} " in f" {doc_key} "]
    if len(present) == 1:
        c = present[0]
        return CustomerMatch(customer_id=c.customer_id, name=c.name, method="company_name", matched_on=c.name, score=100.0)
    return CustomerMatch(customer_id=None, name=extraction.buyer_company.value if extraction else None, method="none")
