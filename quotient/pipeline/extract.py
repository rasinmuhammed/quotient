"""Stage 2, extract: the model reads the document and returns line items and header fields as JSON.

The model only reads and copies. Each value comes with the exact span it was read from, and code checks every
span against the document (and every quantity against the numbers in its span) before anything downstream
uses it. A value whose span cannot be found is treated as unread, and the line goes to a human.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation

from rapidfuzz import fuzz

from quotient.catalog import Catalog
from quotient.llm import LLM, LLMCall
from quotient.pipeline.prompts import system_prompt
from quotient.pipeline.types import ExtractedLine, Extraction, IncotermMention, RFQDocument, Spanned

# anyOf, not a list of types: Groq's strict mode documents anyOf as its union, and a list was not enforced
# (one live answer came back with english_name as an object and failed validation).
TEXT_OR_NULL = {"anyOf": [{"type": "string"}, {"type": "null"}]}


def _spanned(desc: str) -> dict:
    return {"type": "object", "additionalProperties": False, "required": ["value", "span"], "description": desc,
            "properties": {"value": TEXT_OR_NULL, "span": TEXT_OR_NULL}}


LINE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["mention", "english_name", "cas", "grade", "quantity", "unit", "packaging", "confidence", "note"],
    "properties": {
        "mention": _spanned("product as written"), "english_name": TEXT_OR_NULL,
        "cas": _spanned("CAS number as written"), "grade": _spanned("grade as written"),
        "quantity": _spanned("plain number"), "unit": _spanned("unit as written"),
        "packaging": _spanned("pack type as written"), "confidence": {"type": "number"},
        "note": TEXT_OR_NULL,
    },
}
SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["buyer_company", "incoterms", "destination_country", "requested_date", "currency", "document_grade", "lines"],
    "properties": {
        "buyer_company": _spanned("buyer company"),
        "incoterms": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["term", "place", "span"],
            "properties": {"term": {"type": "string"}, "place": TEXT_OR_NULL, "span": TEXT_OR_NULL}}},
        "destination_country": _spanned("ISO alpha-2"), "requested_date": _spanned("yyyy-mm-dd"),
        "currency": _spanned("ISO currency"), "document_grade": _spanned("grade for all lines"),
        "lines": {"type": "array", "items": LINE_SCHEMA},
    },
}


# ------------------------------------------------------------------------------------------------ verification


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).lower()
    s = s.replace("–", "-").replace("—", "-").replace("’", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", s).strip()


def span_found(span: str | None, doc_norm: str) -> bool:
    if not span or not span.strip():
        return False
    s = norm(span)
    if s in doc_norm:
        return True
    return len(s) >= 12 and fuzz.partial_ratio(s, doc_norm) >= 95


# A number as written: grouped thousands ("10,000", "1.000,5", "1'250") or plain ("16", "0.5"). Spaces only
# group thousands in the second pattern, so "RT-92 10,000" stays two numbers.
NUMBER = re.compile(r"\d{1,3}(?:[,.']\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?")
_SPACED = re.compile(r"(?<![\d.,])\d{1,3}(?: \d{3})+(?:[.,]\d+)?")
_PLAIN = re.compile(r"\d+(?:\.\d+)?")


def readings(token: str) -> set[Decimal]:
    """What a written number can mean, with "." or "," as the decimal point: "1.000" is 1 or 1000."""
    out = set()
    for dec, thou in ((".", ",' "), (",", ".' ")):
        t = token
        for ch in thou:
            t = t.replace(ch, "")
        t = t.replace(dec, ".")
        if _PLAIN.fullmatch(t):
            out.add(Decimal(t))
    return out


def numbers_in(text: str) -> set[Decimal]:
    """Every number a span can mean: each token's readings, and "10 000" read as one number too. Nothing is
    derived: for "20 x 25L" the reader gives 20 and the unit, and code works out the 500 L."""
    found = set()
    for m in NUMBER.finditer(text):
        found |= readings(m.group(0))
    for m in _SPACED.finditer(text):
        found |= readings(m.group(0))
    return found


def _quantity_ok(value: str | None, span: str | None) -> bool:
    if value is None or span is None:
        return False
    try:
        v = Decimal(value)
    except InvalidOperation:
        return False
    return v in numbers_in(span)


def _check(sp: dict, doc_norm: str) -> Spanned:
    value, span = sp.get("value"), sp.get("span")
    return Spanned(value=value, span=span, verified=value is not None and span_found(span, doc_norm))


def parse(raw: dict, doc: RFQDocument) -> Extraction:
    doc_norm = norm(doc.text)
    lines = []
    for ln in raw.get("lines", []):
        qty = _check(ln["quantity"], doc_norm)
        qty = qty.model_copy(update={"verified": qty.verified and _quantity_ok(qty.value, qty.span)})
        mention = _check(ln["mention"], doc_norm)
        lines.append(ExtractedLine(
            mention=mention, english_name=ln.get("english_name"), cas=_check(ln["cas"], doc_norm),
            grade=_check(ln["grade"], doc_norm), quantity=qty, unit=_check(ln["unit"], doc_norm),
            packaging=_check(ln["packaging"], doc_norm), confidence=max(0.0, min(1.0, float(ln.get("confidence") or 0))),
            note=ln.get("note")))
    incoterms = tuple(IncotermMention(term=i["term"].strip().upper(), place=i.get("place"), span=i.get("span"),
                                      verified=span_found(i.get("span"), doc_norm)) for i in raw.get("incoterms", []))
    return Extraction(buyer_company=_check(raw["buyer_company"], doc_norm), incoterms=incoterms,
                      destination_country=_check(raw["destination_country"], doc_norm),
                      requested_date=_check(raw["requested_date"], doc_norm), currency=_check(raw["currency"], doc_norm),
                      document_grade=_check(raw["document_grade"], doc_norm), lines=tuple(lines))


def extract(doc: RFQDocument, llm: LLM, catalog: Catalog, as_of: date) -> tuple[Extraction, LLMCall]:
    received = doc.received or as_of
    user = f"TASK: EXTRACT\nDocument date: {received.isoformat()}\nDocument format: {doc.format}\n\n<document>\n{doc.text}\n</document>"
    raw, call = llm.json(name="rfq_extraction", schema=SCHEMA, system=system_prompt(catalog), user=user)
    return parse(raw, doc), call
