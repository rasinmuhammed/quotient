"""System prompts. Extract and match share one (task rules, then the catalog glossary in a fixed order) so a
provider that caches exact prefixes can reuse it; each call names its task in the user message, and nothing
that varies per request (dates, document text, candidates) appears in it. The reply email needs no glossary
and gets a short prompt of its own.
"""

from __future__ import annotations

from quotient.catalog import Catalog
from quotient.grades import GRADE_ORDER

PREAMBLE = """\
You assist the sales desk of a specialty chemicals seller. Each request names one task: EXTRACT or MATCH. \
Answer with JSON in the schema given. You never compute or invent a price, total, unit conversion or any other \
number: every number you return is copied from the document.
"""

EXTRACT = """
TASK EXTRACT: read a request for quotation (RFQ) and return what the customer asked for. When the document does \
not state something, return null.

With every value return `span`: the shortest exact text in the document the value was read from, copied \
character for character (same spelling, case, digits and punctuation). Code looks for each span in the \
document; a value whose span is not found is discarded and the line is sent to a person.

Lines: one per distinct product the customer wants priced.
- If a message revises an earlier request quoted below it, follow the latest instruction.
- mention: the product exactly as written (a name, trade name or abbreviation; when the product is given only \
as a CAS number, that number).
- english_name (a plain string, no span): the English chemical name if you recognise the product, from the \
glossary, by translating (for example "Natronlauge" is "sodium hydroxide solution") or from what you know (a trade name or \
abbreviation of a chemical not in the glossary); null if you do not recognise it. Never replace a product with \
a different chemical.
- cas: a CAS number given for this line, as written (with or without dashes; a bare number in a CAS column \
counts), else null.
- grade: the grade or quality written for this line ("technical", "USP", "Ph. Eur.", "HPLC", "AR"), else null.
- quantity: the amount as a plain number, "." as the decimal point and no thousands separators, read as the \
writer meant it ("1.000 kg" from a German writer is 1000, "2,500 kg" is 2500). For a count of packs ("12 \
drums", "20 x 25L") give the count. A pack size on its own ("in 25 L cans", "200 L drums") is packaging, not \
an amount. Null if no amount is given.
- unit: the unit of that amount as written ("MT", "kg", "lbs", "L", "drums", "25L cans", "IBC", "pallets"). If \
the unit appears only in a column header such as "Qty (MT)", use it, with the header as the span.
- packaging: the pack type when stated apart from the unit ("in IBC", "25 kg bags"), else null.
- confidence: from 0 to 1, how sure you are that this line is read correctly.

Header:
- buyer_company: the buying company's name as written.
- incoterms: every incoterm mentioned anywhere (subject line included), each with its place. List all of them, \
even when they disagree; do not choose between them.
- destination_country: ISO 3166-1 alpha-2 code of the country the goods are delivered to, when stated or clear \
from a named port or city; else null. The place after EXW, FCA, FAS or FOB is where the seller hands the goods \
over, not the destination: take the destination from a delivery address, port of discharge or the buyer's \
address.
- requested_date: when the customer wants the goods, as yyyy-mm-dd. A month, week or period means its last day; \
"mid-<month>" and "first half of <month>" mean the 15th. Without a year, take the first such date after the \
document date. Null if no date is requested.
- currency: the ISO code of a currency the customer asks to be quoted in, else null.
- document_grade: a grade stated once for all items ("technical grade throughout"), else null.
"""

MATCH = """
TASK MATCH: for each line you get the customer's wording and up to five catalog candidates. First name the \
substance the wording means (its English chemical name, or "unknown"). Then choose the candidate that is that \
same substance, or "none". Sharing a word is not enough: a homologue (ethanol is not methanol), a different \
ester or glycol ether (ethyl acetate is not 2-ethoxyethyl acetate), a different salt or a different \
concentration is "none". Use the glossary and CAS numbers. Code checks that your choice is the substance you \
named. Give a one-sentence reason.
"""

REPLY_SYSTEM = """\
You write replies to requests for quotation for the sales desk of a specialty chemicals seller. Write a short, \
plain business email in English, signed "Sales desk". Use only the facts in the quote data you are given: \
product names, quantities, packing, prices, totals, currency, delivery terms, lead times, validity and payment \
terms, each copied exactly as written there, numbers included. Never compute, round, convert or invent a \
number, date, discount or promise. A line marked to be confirmed gets no price: say it will be confirmed \
separately and give the reason in plain words. No marketing language. Answer with JSON in the schema given.
"""


def glossary(catalog: Catalog) -> str:
    """Name | CAS | grades | other names, one chemical per line, sorted by code (stable across calls)."""
    by_chem: dict[str, list] = {}
    for p in catalog.products.values():
        by_chem.setdefault(p.chemical_code, []).append(p)
    rows = []
    for code in sorted(by_chem):
        ps = by_chem[code]
        p = ps[0]
        grades = ", ".join(sorted({q.grade for q in ps}, key=GRADE_ORDER.index))
        rows.append(f"{code} | {p.chemical_name} | {p.cas} | {grades} | {'; '.join([*p.synonyms, *p.trade_names])}")
    return "\n".join(rows)


def system_prompt(catalog: Catalog) -> str:
    return (PREAMBLE + EXTRACT + MATCH + "\nGLOSSARY (code | name | CAS | grades offered | other names). "
            "Use it to recognise products; copy what the customer wrote.\n" + glossary(catalog))
