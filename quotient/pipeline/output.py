"""Stage 6, output: the reply email and the quote PDF.

The model writes the email from a fixed set of facts, every number already formatted by code. The draft is
then checked both ways: each number in the email must occur in the facts (nothing invented), and each price
in the facts must occur in the email (nothing dropped). A draft that fails either check is replaced by a
plain template built from the same facts, and the reason is recorded.
"""

from __future__ import annotations

import io
import json
import re
from datetime import date, timedelta
from decimal import Decimal

from quotient.catalog import Catalog
from quotient.domain import FlagCode, Severity
from quotient.llm import LLM, LLMCall, LLMError
from quotient.pipeline.extract import NUMBER, readings
from quotient.pipeline.prompts import system_prompt
from quotient.pipeline.types import EmailDraft, LineResult, QuoteDraft
from quotient.pricing.rules import RuleSet

VALIDITY_RULE = "OFFER-VALIDITY-v1"

# What the customer is told when a line cannot be priced yet, by the flag that holds it.
CUSTOMER_REASON = {
    FlagCode.NO_MATCH: "we could not identify this product in our range; could you send a CAS number or specification?",
    FlagCode.AMBIGUOUS_MATCH: "we are checking which product you need",
    FlagCode.LOW_CONFIDENCE: "we are checking the details of this item",
    FlagCode.GRADE_NOT_SPECIFIED: "please confirm the grade you need",
    FlagCode.QUANTITY_MISSING: "please confirm the quantity",
    FlagCode.UNIT_CONVERSION: "please confirm the quantity in kg, litres or tonnes",
    FlagCode.AMBIGUOUS_UNIT: "please confirm whether 'tons' means metric tonnes",
    FlagCode.PACK_UNAVAILABLE: "the packing asked for is not available; we will propose an alternative",
    FlagCode.BELOW_MOQ: "the quantity is below our minimum order; we will come back with options",
    FlagCode.PRICE_EXPIRED: "we are confirming the current price",
    FlagCode.NO_PRICE: "we are confirming the price",
    FlagCode.INCOTERM_UNSUPPORTED: "we are checking whether we can offer these delivery terms",
    FlagCode.NO_FREIGHT_LANE: "we are checking freight to your destination",
}


def money(d: Decimal, currency: str) -> str:
    places = 0 if currency == "JPY" else 2
    return f"{d:,.{places}f} {currency}"


def unit_money(d: Decimal, currency: str, uom: str) -> str:
    places = 0 if currency == "JPY" else 3
    return f"{d:,.{places}f} {currency}/{uom}"


def qty(d: Decimal, uom: str) -> str:
    return f"{d.normalize():,f} {uom}"


def offer_valid_until(draft_lines: tuple[LineResult, ...], quote_date: date, catalog: Catalog, rules: RuleSet) -> date:
    days = int(rules.param(VALIDITY_RULE, "validity_days"))
    until = quote_date + timedelta(days=days)
    for ln in draft_lines:
        p = ln.priced
        if p is None or p.price_row_id is None:
            continue
        row = next(r for r in catalog.price_rows[(p.sku, p.pack_code)] if r.price_row_id == p.price_row_id)
        if row.valid_from <= quote_date <= row.valid_to:
            until = min(until, row.valid_to)
    return until


def _place(draft: QuoteDraft, catalog: Catalog) -> str | None:
    h = draft.header
    if draft.extraction:
        for m in draft.extraction.incoterms:
            if m.verified and m.term.upper() == h.incoterm and m.place:
                return m.place
    for ln in draft.lines:
        if ln.priced and ln.priced.freight_row_id:
            f = next(f for f in catalog.freight.values() if f.freight_row_id == ln.priced.freight_row_id)
            return f.origin_port_name if h.incoterm == "FOB" else f.destination_port_name
    return None


def reply_facts(draft: QuoteDraft, catalog: Catalog) -> dict:
    """Everything the email may say, with every number already formatted."""
    h, cur = draft.header, draft.currency
    customer = catalog.customers.get(h.customer.customer_id) if h.customer.customer_id else None
    lines, confirmed = [], []
    for ln in draft.lines:
        wording = ln.extracted.mention.value if ln.extracted else None
        p = ln.priced
        if p is not None and p.priced and not ln.needs_human:
            confirmed.append(p.line_total)
            lines.append({"no": ln.line_no, "your_wording": wording, "product": p.product_name, "sku": p.sku,
                          "quantity": qty(p.quoted_qty, p.uom), "packing": f"{p.packs} x {p.pack_description}",
                          "unit_price": unit_money(p.unit_price, cur, p.uom), "line_total": money(p.line_total, cur),
                          "lead_time": f"{p.lead_time_days} days from order"})
        else:
            hold = next((f for f in ln.flags if f.severity == Severity.BLOCK and f.code in CUSTOMER_REASON), None)
            lines.append({"no": ln.line_no, "your_wording": wording,
                          "product": p.product_name if p else None, "status": "to be confirmed separately",
                          "reason": CUSTOMER_REASON[hold.code] if hold else "we are checking this item"})
    facts = {
        "rfq_subject": draft.document.subject,
        "customer_company": customer.name if customer else h.customer.name,
        "contact_name": customer.contact_name if customer else draft.document.sender_name,
        "our_reference": draft.id,
        "delivery_terms": " ".join(x for x in (h.incoterm, _place(draft, catalog)) if x),
        "currency": cur,
        "lines": lines,
        "total_of_priced_lines": money(sum(confirmed, Decimal(0)), cur) if confirmed else None,
        "offer_valid_until": draft.valid_until.strftime("%d %B %Y") if draft.valid_until else None,
        "payment_terms": customer.payment_terms if customer else None,
    }
    conflict = next((f for f in draft.quote_flags if f.code == FlagCode.INCOTERM_CONFLICT), None)
    if conflict and len(h.incoterms_mentioned) > 1:
        others = [t for t in h.incoterms_mentioned if t != h.incoterm]
        facts["terms_note"] = (f"Your message mentions {' and '.join(h.incoterms_mentioned)}; this offer is {h.incoterm}. "
                               f"Tell us if you prefer {' or '.join(others)}.")
    return facts


# ------------------------------------------------------------------------------------------------ number check

# In an email, a number glued to a word or code ("RT-92", "Q-261006-3F9A1C") is part of a name, not a figure.
_FIGURE = re.compile(r"(?<![\w.,'-])(?:" + NUMBER.pattern + ")")


def _numbers(text: str) -> list[tuple[str, set[Decimal]]]:
    return [(m.group(0), readings(m.group(0))) for m in _FIGURE.finditer(text)]


def _strings(obj) -> list[str]:
    if isinstance(obj, dict):
        return [s for v in obj.values() for s in _strings(v)]
    if isinstance(obj, list):
        return [s for v in obj for s in _strings(v)]
    return [str(obj)] if obj is not None else []


def check_numbers(subject: str, body: str, facts: dict) -> list[str]:
    """Problems with an email draft: numbers it states that the facts do not, and prices it leaves out."""
    allowed: set[Decimal] = set()
    for s in _strings(facts):
        for _, r in _numbers(s):
            allowed |= r
    text = f"{subject}\n{body}"
    problems = [f"'{tok}' is not in the quote data" for tok, r in _numbers(text) if not r & allowed]
    stated = {d for _, r in _numbers(text) for d in r}
    for ln in facts["lines"]:
        for key in ("unit_price", "line_total"):
            if key in ln and not readings(NUMBER.search(ln[key]).group(0)) & stated:
                problems.append(f"line {ln['no']} {key.replace('_', ' ')} {ln[key]} is missing")
    if facts["total_of_priced_lines"] and not readings(NUMBER.search(facts["total_of_priced_lines"]).group(0)) & stated:
        problems.append(f"total {facts['total_of_priced_lines']} is missing")
    return problems


# ------------------------------------------------------------------------------------------------ the email

EMAIL_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["subject", "body"],
                "properties": {"subject": {"type": "string"}, "body": {"type": "string"}}}


def template_email(facts: dict) -> tuple[str, str]:
    subject = f"Re: {facts['rfq_subject']}" if facts["rfq_subject"] else "Our quotation"
    subject += f" [{facts['our_reference']}]"
    greeting = f"Dear {facts['contact_name']}," if facts["contact_name"] else "Dear Sir or Madam,"
    out = [greeting, "", f"Thank you for your enquiry. Our offer, {facts['delivery_terms']}, in {facts['currency']}:", ""]
    held = []
    for ln in facts["lines"]:
        if "unit_price" in ln:
            out.append(f"{ln['no']}. {ln['product']}: {ln['quantity']} ({ln['packing']}) at {ln['unit_price']}, "
                       f"total {ln['line_total']}. Lead time {ln['lead_time']}.")
        else:
            held.append(f"- {ln['your_wording'] or ln['product']}: {ln['reason']}")
    if facts["total_of_priced_lines"]:
        out += ["", f"Total: {facts['total_of_priced_lines']}"]
    if held:
        out += ["", "To be confirmed separately:", *held]
    if facts.get("terms_note"):
        out += ["", facts["terms_note"]]
    tail = [f"Offer valid until {facts['offer_valid_until']}." if facts["offer_valid_until"] else None,
            f"Payment terms: {facts['payment_terms']}." if facts["payment_terms"] else None]
    out += ["", " ".join(t for t in tail if t), "", "Kind regards,", "Sales desk"]
    return subject, "\n".join(out).replace("\n\n\n", "\n\n")


def draft_email(draft: QuoteDraft, catalog: Catalog, llm: LLM | None) -> tuple[EmailDraft, LLMCall | None]:
    facts = reply_facts(draft, catalog)
    if llm is None:
        subject, body = template_email(facts)
        return EmailDraft(subject=subject, body=body, source="template", check="template; no model configured"), None
    user = "TASK: REPLY\nQuote data:\n" + json.dumps(facts, ensure_ascii=False, indent=1)
    try:
        raw, call = llm.json(name="reply_email", schema=EMAIL_SCHEMA, system=system_prompt(catalog), user=user)
    except LLMError as exc:
        subject, body = template_email(facts)
        return EmailDraft(subject=subject, body=body, source="template", check=f"template; model failed: {exc}"), None
    subject, body = raw["subject"].strip(), raw["body"].strip()
    problems = check_numbers(subject, body, facts)
    if problems:
        t_subject, t_body = template_email(facts)
        return EmailDraft(subject=t_subject, body=t_body, source="template",
                          check="model draft rejected: " + "; ".join(problems[:5])), call
    n = len(_numbers(f"{subject}\n{body}"))
    return EmailDraft(subject=subject, body=body, source="llm",
                      check=f"all {n} numbers occur in the quote data; every price is present"), call


# ------------------------------------------------------------------------------------------------ PDF


def quote_pdf(draft: QuoteDraft, catalog: Catalog) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    facts = reply_facts(draft, catalog)
    styles = getSampleStyleSheet()
    small = styles["Normal"].clone("small", fontSize=8, leading=10, textColor=colors.HexColor("#555555"))
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=16 * mm,
                            title=f"Quotation {draft.id}", author="Quotient sales desk", invariant=True)
    head = [["Quotation", draft.id], ["Date", draft.header.quote_date.strftime("%d %B %Y")],
            ["Customer", facts["customer_company"] or "-"], ["Delivery terms", facts["delivery_terms"]],
            ["Currency", facts["currency"]], ["Valid until", facts["offer_valid_until"] or "-"],
            ["Payment terms", facts["payment_terms"] or "-"]]
    rows = [["#", "Product", "Quantity", "Packing", "Unit price", "Total"]]
    for ln in facts["lines"]:
        if "unit_price" in ln:
            rows.append([ln["no"], Paragraph(f"{ln['product']}<br/><font size=7>{ln['sku']}</font>", styles["Normal"]),
                         ln["quantity"], Paragraph(ln["packing"], styles["Normal"]), ln["unit_price"], ln["line_total"]])
        else:
            rows.append([ln["no"], Paragraph(ln["your_wording"] or ln["product"] or "-", styles["Normal"]),
                         Paragraph(f"<i>to be confirmed: {ln['reason']}</i>", small), "", "", ""])
    if facts["total_of_priced_lines"]:
        rows.append(["", "", "", "", "Total", facts["total_of_priced_lines"]])
    items = Table(rows, colWidths=[8 * mm, 58 * mm, 26 * mm, 36 * mm, 26 * mm, 24 * mm], repeatRows=1)
    items.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.black), ("LINEBELOW", (0, 1), (-1, -1), 0.25, colors.HexColor("#CCCCCC")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"), ("ALIGN", (4, 0), (5, -1), "RIGHT"),
    ]))
    meta = Table(head, colWidths=[34 * mm, 120 * mm])
    meta.setStyle(TableStyle([("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"), ("FONTSIZE", (0, 0), (-1, -1), 9)]))
    story = [Paragraph("Quotation", styles["Title"]), meta, Spacer(1, 6 * mm), items, Spacer(1, 6 * mm)]
    if facts.get("terms_note"):
        story.append(Paragraph(facts["terms_note"], styles["Normal"]))
    story.append(Paragraph(f"Prices under pricing rules {draft.rules_version}; currency converted at the "
                           f"{catalog.fx_snapshot_date:%d %B %Y} FX snapshot. Quantities are rounded up to whole packs.", small))
    doc.build(story)
    return buf.getvalue()
