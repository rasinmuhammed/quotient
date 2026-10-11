"""What each pipeline stage produces. The review screen and the evals read these same records."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import Field as PydField
from pydantic import computed_field

from quotient.domain import Flag, Model, PricedLine, Severity

# ------------------------------------------------------------------------------------------------ ingest


class RFQDocument(Model):
    """One RFQ as text the model reads. Every span the extractor returns must be found in `text`."""

    source_name: str
    format: Literal["text", "eml", "pdf", "xlsx"]
    text: str
    sender_email: str | None = None
    sender_name: str | None = None
    subject: str | None = None
    received: date | None = None


# ------------------------------------------------------------------------------------------------ extract


class Spanned(Model):
    value: str | None
    span: str | None  # the exact text the value was read from
    verified: bool = False  # set by code: the span occurs in the document


class IncotermMention(Model):
    term: str
    place: str | None
    span: str | None
    verified: bool = False


class ExtractedLine(Model):
    mention: Spanned
    english_name: str | None
    cas: Spanned
    grade: Spanned
    quantity: Spanned
    unit: Spanned
    packaging: Spanned
    confidence: float
    note: str | None = None


class Extraction(Model):
    buyer_company: Spanned
    incoterms: tuple[IncotermMention, ...]
    destination_country: Spanned
    requested_date: Spanned
    currency: Spanned
    document_grade: Spanned
    lines: tuple[ExtractedLine, ...]


# ------------------------------------------------------------------------------------------------ match

# translation: the extractor's English name for a foreign or garbled mention hit the synonym table.
# manual: a person chose the SKU on the review screen.
MatchMethod = Literal["cas", "sku", "synonym", "translation", "fuzzy", "llm", "manual", "none"]


class Candidate(Model):
    chemical_code: str
    name: str
    score: float


class Match(Model):
    chemical_code: str | None
    sku: str | None
    method: MatchMethod
    matched_on: str | None = None  # the text that matched (a CAS number, a synonym, the extractor's English name)
    score: float | None = None
    grade: str | None = None
    grade_source: Literal["line", "document", "only_grade", "default", "manual", "none"] = "none"
    candidates: tuple[Candidate, ...] = ()
    explanation: str = ""


class CustomerMatch(Model):
    customer_id: str | None
    name: str | None
    method: Literal["email_domain", "company_name", "manual", "none"]
    matched_on: str | None = None
    score: float | None = None


# ------------------------------------------------------------------------------------------------ normalise


class Quantity(Model):
    value: Decimal | None  # in the SKU's unit of measure
    uom: str | None
    pack_code: str | None  # the pack the customer asked for, if any
    steps: tuple[str, ...] = ()


# ------------------------------------------------------------------------------------------------ the draft


class Header(Model):
    customer: CustomerMatch
    incoterms_mentioned: tuple[str, ...]
    incoterm: str
    incoterm_source: Literal["document", "customer_default", "conflict_customer_default", "conflict_first", "fallback",
                             "manual"]
    destination_country: str | None
    destination_source: Literal["document", "customer", "manual", "none"]
    currency: str
    currency_source: Literal["document", "customer", "fallback", "manual"]
    requested_date: date | None
    quote_date: date


class LineResult(Model):
    line_no: int
    extracted: ExtractedLine | None  # None for a line a person added on the review screen
    match: Match
    quantity: Quantity
    priced: PricedLine | None
    reading_flags: tuple[Flag, ...]  # from reading, matching and converting; the engine's are on `priced`
    edited: bool = False
    approved: bool = False  # a person accepted the line's remaining flags

    @computed_field
    @property
    def flags(self) -> tuple[Flag, ...]:
        """Every flag on the line, one per code, reading flags first."""
        seen: dict[str, Flag] = {}
        for f in (*self.reading_flags, *(self.priced.flags if self.priced else ())):
            seen.setdefault(f.code, f)
        return tuple(seen.values())

    @computed_field
    @property
    def needs_human(self) -> bool:
        return not self.approved and any(f.severity == Severity.BLOCK for f in self.flags)


class StageTiming(Model):
    stage: str
    seconds: float


class LLMUsage(Model):
    calls: int = 0
    prompt_tokens: int = 0
    cached_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    cost_usd: Decimal = Decimal(0)
    seconds: float = 0.0
    waited_seconds: float = 0.0  # rate-limit waits, kept out of `seconds`
    models: tuple[str, ...] = ()


class EmailDraft(Model):
    subject: str
    body: str
    source: Literal["llm", "template"]
    check: str  # what the number check found


class QuoteDraft(Model):
    id: str
    document: RFQDocument
    extraction: Extraction | None
    header: Header
    lines: tuple[LineResult, ...]
    quote_flags: tuple[Flag, ...]
    header_approved: bool = False  # a person accepted the quote-level flags
    currency: str
    total: Decimal | None
    valid_until: date | None
    email: EmailDraft | None = None
    timings: tuple[StageTiming, ...] = ()
    llm: LLMUsage = PydField(default_factory=LLMUsage)
    rules_version: str
    data_version: str
    model: str

    @computed_field
    @property
    def needs_human(self) -> bool:
        header = not self.header_approved and any(f.severity == Severity.BLOCK for f in self.quote_flags)
        return header or any(ln.needs_human for ln in self.lines)
