"""Stage 4, normalise: the quote header (incoterm, destination, currency, dates) and each line's quantity in the
SKU's unit of measure. Plain rules over verified extracted values; each choice records where it came from.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation

from quotient.catalog import Catalog
from quotient.domain import Flag, FlagCode, Product
from quotient.pipeline.extract import norm
from quotient.pipeline.types import CustomerMatch, ExtractedLine, Extraction, Header, Quantity
from quotient.pricing.units import is_pack_word, pack_from_packaging, pack_word_in, sized_pack, to_uom

INCOTERM_RULE = "INCOTERM-v1"
INCOTERMS_2020 = {"EXW", "FCA", "FAS", "FOB", "CFR", "CIF", "CPT", "CIP", "DAP", "DPU", "DDP"}
INCOTERM_ALIASES = {"C&F": "CFR", "CNF": "CFR", "C+F": "CFR", "C AND F": "CFR"}
FALLBACK_INCOTERM = "EXW"
ORIGIN_TERMS = {"EXW", "FCA", "FAS", "FOB"}  # the named place is on the seller's side
_WEEK = re.compile(r"\b(?:kw|cw|wk|week|woche|kalenderwoche|calendar week)\.?\s*(\d{1,2})\b(?:\s*[/']\s*(\d{4}|\d{2})\b|\s+(\d{4})\b)?",
                   re.IGNORECASE)


def week_end(text: str, after: date) -> date | None:
    """'KW 48', 'week 12/2027', 'CW48': the Sunday that ends that ISO week. Without a year, the first such week
    ending on or after `after`. Calendar arithmetic is code's job, not the reader's."""
    m = _WEEK.search(text)
    if not m:
        return None
    week, y = int(m.group(1)), m.group(2) or m.group(3)
    years = [int(y) + (2000 if len(y) == 2 else 0)] if y else [after.year, after.year + 1]
    for year in years:
        try:
            end = date.fromisocalendar(year, week, 7)
        except ValueError:
            continue
        if y or end >= after:
            return end
    return None


def _incoterm(term: str) -> str | None:
    t = re.sub(r"\s+", " ", term.strip().upper())
    t = INCOTERM_ALIASES.get(t, t)
    return t if t in INCOTERMS_2020 else None


def resolve_header(extraction: Extraction | None, customer: CustomerMatch, catalog: Catalog,
                   quote_date: date) -> tuple[Header, tuple[Flag, ...]]:
    known = catalog.customers.get(customer.customer_id) if customer.customer_id else None
    flags: list[Flag] = []

    mentioned = list(dict.fromkeys(t for m in (extraction.incoterms if extraction else ()) if m.verified
                                   and (t := _incoterm(m.term))))
    if len(mentioned) == 1:
        incoterm, source = mentioned[0], "document"
    elif mentioned:
        if known and known.default_incoterm in mentioned:
            incoterm, source = known.default_incoterm, "conflict_customer_default"
        else:
            incoterm, source = mentioned[0], "conflict_first"
        flags.append(Flag.of(FlagCode.INCOTERM_CONFLICT, f"the RFQ names {' and '.join(mentioned)}; priced {incoterm} "
                             f"({'the customer’s usual term' if source == 'conflict_customer_default' else 'named first'})",
                             INCOTERM_RULE))
    elif known:
        incoterm, source = known.default_incoterm, "customer_default"
    else:
        incoterm, source = FALLBACK_INCOTERM, "fallback"

    dest = extraction.destination_country if extraction else None
    # "FOB Nhava Sheva" names the port of loading; a destination read from it is the seller's country
    origin_places = [norm(m.place) for m in (extraction.incoterms if extraction else ())
                     if m.verified and m.place and m.place.strip() and _incoterm(m.term) in ORIGIN_TERMS]
    from_origin = bool(dest and dest.span and any(place in norm(dest.span) for place in origin_places))
    if dest and dest.verified and dest.value and re.fullmatch(r"[A-Za-z]{2}", dest.value.strip()) and not from_origin:
        destination, dest_source = dest.value.strip().upper(), "document"
    elif known:
        destination, dest_source = known.country_code, "customer"
    else:
        destination, dest_source = None, "none"

    cur = extraction.currency if extraction else None
    asked = cur.value.strip().upper() if cur and cur.verified and cur.value else None
    if asked in catalog.fx:
        currency, cur_source = asked, "document"
    elif known:
        currency, cur_source = known.currency, "customer"
    else:
        currency, cur_source = "USD", "fallback"

    requested = None
    rd = extraction.requested_date if extraction else None
    if rd and rd.verified and rd.value:
        try:
            requested = week_end(rd.span or "", quote_date) or date.fromisoformat(rd.value.strip())
        except ValueError:
            requested = None

    if known is None:
        flags.append(Flag.of(FlagCode.CUSTOMER_UNKNOWN, "sender is not in the customer list: list price, no discount",
                             "DISC-TIER-v1"))
    header = Header(customer=customer, incoterms_mentioned=tuple(mentioned), incoterm=incoterm, incoterm_source=source,
                    destination_country=destination, destination_source=dest_source, currency=currency,
                    currency_source=cur_source, requested_date=requested, quote_date=quote_date)
    return header, tuple(flags)


def _plain_number(text: str | None) -> Decimal | None:
    if text is None:
        return None
    try:
        d = Decimal(text.strip())
    except InvalidOperation:
        return None
    return d if d.is_finite() and d > 0 else None


def resolve_quantity(line: ExtractedLine, product: Product | None, catalog: Catalog,
                     country_code: str | None) -> tuple[Quantity, tuple[Flag, ...]]:
    """The line's amount in the SKU's unit of measure, and the pack the customer asked for, if any."""
    if line.quantity.value is None:
        return Quantity(value=None, uom=product.uom if product else None, pack_code=None), (
            Flag.of(FlagCode.QUANTITY_MISSING, "the RFQ gives no quantity for this line", "UNIT-CONV-v1"),)
    value = _plain_number(line.quantity.value)
    if product is None or value is None:  # unmatched, or not a number: nothing to convert (review flags the latter)
        return Quantity(value=None, uom=product.uom if product else None, pack_code=None), ()

    unit, packaging = line.unit.value, line.packaging.value
    steps: list[str] = []
    if not unit and packaging and is_pack_word(packaging):
        unit = packaging
        steps.append(f"no unit given; read as a count of '{packaging}'")
    if not unit:
        return Quantity(value=None, uom=product.uom, pack_code=None), (
            Flag.of(FlagCode.UNIT_CONVERSION, f"'{line.quantity.value}' has no unit", "UNIT-CONV-v1"),)
    if not re.search(r"\d", unit) and pack_word_in(unit):
        sized = sized_pack(f"{line.quantity.value} {unit}", product.form, catalog.packs)
        if sized and sized[2]:  # "in 25 L cans": the number is the size of one pack we sell, not an amount
            return Quantity(value=None, uom=product.uom, pack_code=sized[2]), (Flag.of(
                FlagCode.QUANTITY_MISSING, f"'{line.quantity.value} {unit}' is a pack size; the RFQ gives no amount",
                "UNIT-CONV-v1"),)

    conv = to_uom(value, unit, product, catalog.packs, country_code)
    flags = list(conv.flags)
    pack_code = conv.pack_code
    steps += conv.steps
    if packaging and packaging != unit:
        asked, pack_flags = pack_from_packaging(packaging, product, catalog.packs)
        if pack_code is None:
            pack_code = asked
            flags += pack_flags
        elif asked and asked != pack_code:
            steps.append(f"packaging '{packaging}' differs from the unit '{unit}'; the unit's pack is used")
    return Quantity(value=conv.quantity, uom=product.uom, pack_code=pack_code, steps=tuple(steps)), tuple(flags)
