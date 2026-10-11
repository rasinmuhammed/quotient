"""The pipeline end to end: ingest -> extract -> match -> normalise -> price -> review -> output.

    draft = Pipeline.default().run(ingest(data, "rfq.eml"))

The model is called at most three times per RFQ (extract, a batched match for lines the rules could not
match, the reply email); everything between is deterministic. `reprice` applies a person's edits on the
review screen and re-runs only the deterministic stages, so an edit never costs a model call.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from quotient import tracing
from quotient.catalog import Catalog, default_catalog
from quotient.domain import Flag, FlagCode, PricingRequest
from quotient.llm import LLM, LLMCall
from quotient.pipeline.extract import extract
from quotient.pipeline.match import match_customer, match_lines
from quotient.pipeline.normalise import resolve_header, resolve_quantity
from quotient.pipeline.output import draft_email, offer_valid_until
from quotient.pipeline.types import (
    CustomerMatch,
    ExtractedLine,
    Header,
    LineResult,
    LLMUsage,
    Match,
    Model,
    Quantity,
    QuoteDraft,
    RFQDocument,
    StageTiming,
)
from quotient.pricing.engine import PricingEngine, quote_total
from quotient.pricing.rules import RuleSet, default_rules

VERIFY_RULE = "EXTRACT-VERIFY-v1"
RULES_USED = (VERIFY_RULE, "MATCH-v1", "GRADE-DEFAULT-v1", "INCOTERM-v1", "DESTINATION-v1", "CURRENCY-v1",
              "OFFER-VALIDITY-v1")

# Flags a person's edit settles: a chosen SKU settles how the line was read and matched, an entered quantity
# settles how it was read and converted, a chosen pack settles the pack request. The engine's flags are
# recomputed on every reprice, so they are never carried over.
SETTLED_BY_SKU = {FlagCode.NO_MATCH, FlagCode.AMBIGUOUS_MATCH, FlagCode.GRADE_NOT_SPECIFIED, FlagCode.LOW_CONFIDENCE}
SETTLED_BY_QTY = {FlagCode.QUANTITY_MISSING, FlagCode.UNIT_CONVERSION, FlagCode.AMBIGUOUS_UNIT, FlagCode.LOW_CONFIDENCE}
SETTLED_BY_PACK = {FlagCode.PACK_UNAVAILABLE}
CONVERTED = {FlagCode.QUANTITY_MISSING, FlagCode.UNIT_CONVERSION, FlagCode.AMBIGUOUS_UNIT, FlagCode.PACK_UNAVAILABLE}


class LineEdit(Model):
    line_no: int | None = None  # None adds a line
    sku: str | None = None
    quantity: Decimal | None = None  # in the SKU's unit of measure
    pack_code: str | None = None
    approved: bool | None = None
    remove: bool = False


class Edits(Model):
    customer_id: str | None = None
    incoterm: str | None = None
    destination_country: str | None = None
    currency: str | None = None
    header_approved: bool | None = None
    lines: tuple[LineEdit, ...] = ()


def review_flags(line: ExtractedLine, min_confidence: float) -> tuple[Flag, ...]:
    """A line goes to a person when its product or amount could not be found in the document as read."""
    why = []
    if not line.mention.verified:
        why.append("the product name was not found in the document")
    if line.quantity.value is not None and not line.quantity.verified:
        why.append(f"the quantity '{line.quantity.value}' was not found in the document")
    if line.unit.value is not None and not line.unit.verified:
        why.append(f"the unit '{line.unit.value}' was not found in the document")
    if line.confidence < min_confidence:
        why.append(f"the reader's confidence is {line.confidence:.2f}")
    return (Flag.of(FlagCode.LOW_CONFIDENCE, "; ".join(why), VERIFY_RULE),) if why else ()


def usage(calls: list[LLMCall]) -> LLMUsage:
    return LLMUsage(calls=len(calls), prompt_tokens=sum(c.prompt_tokens for c in calls),
                    cached_tokens=sum(c.cached_tokens for c in calls),
                    completion_tokens=sum(c.completion_tokens for c in calls),
                    reasoning_tokens=sum(c.reasoning_tokens for c in calls),
                    cost_usd=sum((c.cost_usd for c in calls), Decimal(0)), seconds=round(sum(c.seconds for c in calls), 3),
                    waited_seconds=round(sum(c.waited_seconds for c in calls), 3),
                    models=tuple(dict.fromkeys(c.model for c in calls)))


@dataclass
class _Clock:
    timings: list[StageTiming] = field(default_factory=list)

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        t = time.perf_counter()
        try:
            yield
        finally:
            self.timings.append(StageTiming(stage=name, seconds=round(time.perf_counter() - t, 4)))


@dataclass
class Pipeline:
    catalog: Catalog
    rules: RuleSet
    llm: LLM | None
    engine: PricingEngine = field(init=False)

    def __post_init__(self) -> None:
        self.rules.require(*RULES_USED)
        self.engine = PricingEngine(self.catalog, self.rules)

    @classmethod
    def default(cls, llm: LLM | None = None) -> Pipeline:
        return cls(default_catalog(), default_rules(), llm)

    # ------------------------------------------------------------------------------------------- one RFQ

    def run(self, doc: RFQDocument, quote_date: date | None = None, *, reply: bool = True) -> QuoteDraft:
        with tracing.span("rfq", input={"source": doc.source_name, "format": doc.format},
                          metadata={"rules": self.rules.version, "data": self.catalog.version}) as obs:
            draft = self._run(doc, quote_date, reply=reply)
            if obs is not None:
                obs.update(output={"id": draft.id, "lines": len(draft.lines), "needs_human": draft.needs_human,
                                   "total": str(draft.total), "currency": draft.currency})
        return draft

    def _run(self, doc: RFQDocument, quote_date: date | None, *, reply: bool) -> QuoteDraft:
        if self.llm is None:
            raise ValueError("reading an RFQ needs a model; set GROQ_API_KEY")
        quote_date = quote_date or doc.received or date.today()
        clock, calls = _Clock(), []
        min_conf = float(self.rules.param(VERIFY_RULE, "min_confidence"))

        with clock.stage("extract"):
            extraction, call = extract(doc, self.llm, self.catalog, quote_date)
            calls.append(call)
        with clock.stage("match"):
            customer = match_customer(doc, extraction, self.catalog)
            matches, match_flags, match_calls = match_lines(extraction, self.catalog, self.llm)
            calls += match_calls
        with clock.stage("normalise"):
            header, quote_flags = resolve_header(extraction, customer, self.catalog, quote_date)
            if not extraction.lines:
                quote_flags += (Flag.of(FlagCode.LOW_CONFIDENCE, "no product lines were found in the RFQ", VERIFY_RULE),)
            quantities = [resolve_quantity(ln, self.catalog.products.get(m.sku) if m.sku else None, self.catalog,
                                           header.destination_country) for ln, m in zip(extraction.lines, matches, strict=True)]
        with clock.stage("price"):
            lines = []
            for i, (ln, m, mf, (q, qf)) in enumerate(zip(extraction.lines, matches, match_flags, quantities, strict=True), 1):
                flags = (*review_flags(ln, min_conf), *mf, *qf)
                lines.append(LineResult(line_no=i, extracted=ln, match=m, quantity=q, reading_flags=flags,
                                        priced=self._price(m.sku, q, header)))
        draft = self._assemble(f"Q-{quote_date:%y%m%d}-{hashlib.sha256(doc.text.encode()).hexdigest()[:6].upper()}",
                               doc, extraction, header, tuple(lines), quote_flags, header_approved=False,
                               timings=clock.timings, llm_usage=usage(calls))
        if reply:
            with clock.stage("reply"):
                email, call = draft_email(draft, self.catalog, self.llm)
            calls += [call] if call else []
            draft = draft.model_copy(update={"email": email, "timings": tuple(clock.timings), "llm": usage(calls)})
        return draft

    def _price(self, sku: str | None, q: Quantity, header: Header):
        if sku is None or q.value is None or q.value <= 0:
            return None
        return self.engine.price(PricingRequest(
            sku=sku, quantity=q.value, pack_code=q.pack_code, customer_id=header.customer.customer_id,
            incoterm=header.incoterm, destination_country_code=header.destination_country, currency=header.currency,
            quote_date=header.quote_date))

    def _assemble(self, draft_id: str, doc: RFQDocument, extraction, header: Header, lines: tuple[LineResult, ...],
                  quote_flags: tuple[Flag, ...], *, header_approved: bool, timings, llm_usage: LLMUsage) -> QuoteDraft:
        priced = [ln.priced for ln in lines if ln.priced is not None]
        return QuoteDraft(
            id=draft_id, document=doc, extraction=extraction, header=header, lines=lines, quote_flags=quote_flags,
            header_approved=header_approved, currency=header.currency, total=quote_total(priced),
            valid_until=offer_valid_until(lines, header.quote_date, self.catalog, self.rules), timings=tuple(timings),
            llm=llm_usage, rules_version=self.rules.version,
            data_version=self.catalog.version, model=self.llm.model if self.llm else "none")

    # ------------------------------------------------------------------------------------------- edits

    def reprice(self, draft: QuoteDraft, edits: Edits, *, reply: bool = False) -> QuoteDraft:
        """Apply a person's edits and price again. Deterministic; the email is redrafted only on request."""
        h = draft.header
        quote_flags = list(draft.quote_flags)
        customer = h.customer
        if edits.customer_id is not None:
            c = self.catalog.customers[edits.customer_id]
            customer = CustomerMatch(customer_id=c.customer_id, name=c.name, method="manual", matched_on=c.name)
            quote_flags = [f for f in quote_flags if f.code != FlagCode.CUSTOMER_UNKNOWN]
        header = h.model_copy(update={"customer": customer})
        if edits.incoterm is not None:
            header = header.model_copy(update={"incoterm": edits.incoterm.upper(), "incoterm_source": "manual"})
            quote_flags = [f for f in quote_flags if f.code != FlagCode.INCOTERM_CONFLICT]
        if edits.destination_country is not None:
            header = header.model_copy(update={"destination_country": edits.destination_country.upper(),
                                               "destination_source": "manual"})
        if edits.currency is not None:
            if edits.currency.upper() not in self.catalog.fx:
                raise ValueError(f"no FX rate for {edits.currency}")
            header = header.model_copy(update={"currency": edits.currency.upper(), "currency_source": "manual"})

        by_no = {e.line_no: e for e in edits.lines if e.line_no is not None}
        lines = []
        for ln in draft.lines:
            e = by_no.get(ln.line_no)
            if e and e.remove:
                continue
            lines.append(self._edit_line(ln, e, header))
        next_no = max((ln.line_no for ln in draft.lines), default=0) + 1
        for e in (e for e in edits.lines if e.line_no is None):
            if e.sku is None:
                raise ValueError("a new line needs a SKU")
            blank = LineResult(line_no=next_no, extracted=None, match=Match(chemical_code=None, sku=None, method="none"),
                               quantity=Quantity(value=None, uom=None, pack_code=None), priced=None, reading_flags=())
            lines.append(self._edit_line(blank, e, header))
            next_no += 1

        header_approved = draft.header_approved if edits.header_approved is None else edits.header_approved
        out = self._assemble(draft.id, draft.document, draft.extraction, header, tuple(lines), tuple(quote_flags),
                             header_approved=header_approved, timings=draft.timings, llm_usage=draft.llm)
        if reply:
            email, _ = draft_email(out, self.catalog, self.llm)
            out = out.model_copy(update={"email": email})
        return out

    def _edit_line(self, ln: LineResult, e: LineEdit | None, header: Header) -> LineResult:
        match, quantity, flags = ln.match, ln.quantity, list(ln.reading_flags)
        changed = False
        if e is not None and e.sku is not None and e.sku != ln.match.sku:
            p = self.catalog.product(e.sku)
            match = Match(chemical_code=p.chemical_code, sku=p.sku, method="manual", matched_on=p.name, grade=p.grade,
                          grade_source="manual", candidates=ln.match.candidates, explanation="chosen on the review screen")
            # the amount as the customer wrote it is converted again for the chosen product
            flags = [f for f in flags if f.code not in SETTLED_BY_SKU | CONVERTED]
            if ln.extracted is not None:
                quantity, qflags = resolve_quantity(ln.extracted, p, self.catalog, header.destination_country)
                flags += qflags
            elif e.quantity is None:
                quantity = Quantity(value=None, uom=p.uom, pack_code=None)
                flags.append(Flag.of(FlagCode.QUANTITY_MISSING, f"enter the quantity in {p.uom}", "UNIT-CONV-v1"))
            changed = True
        if e is not None and e.quantity is not None:
            if e.quantity <= 0:
                raise ValueError("quantity must be positive")
            uom = self.catalog.product(match.sku).uom if match.sku else None
            quantity = Quantity(value=e.quantity, uom=uom, pack_code=quantity.pack_code,
                                steps=("entered on the review screen",))
            flags = [f for f in flags if f.code not in SETTLED_BY_QTY]
            changed = True
        if e is not None and e.pack_code is not None:
            quantity = quantity.model_copy(update={"pack_code": e.pack_code})
            flags = [f for f in flags if f.code not in SETTLED_BY_PACK]
            changed = True
        approved = ln.approved and not changed  # an edit reopens the line: what was approved was the old version
        if e is not None and e.approved is not None:
            approved = e.approved
        return ln.model_copy(update={"match": match, "quantity": quantity, "reading_flags": tuple(flags),
                                     "edited": ln.edited or changed,
                                     "approved": approved, "priced": self._price(match.sku, quantity, header)})
