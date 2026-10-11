"""The pipeline's deterministic stages, driven by scripted model answers."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from evals.oracle import oracle_llm
from quotient.domain import FlagCode
from quotient.llm import ScriptedLLM
from quotient.pipeline.ingest import ingest, ingest_text
from quotient.pipeline.normalise import week_end
from quotient.pipeline.output import check_numbers, draft_email, reply_facts
from quotient.pipeline.run import Edits, LineEdit, Pipeline

EVALS = Path(__file__).resolve().parents[1] / "evals"
GOLD = sorted((EVALS / "gold").glob("rfq-*.json"))


def run_oracle(path: Path, catalog, rules):
    gold = json.loads(path.read_text())
    doc = ingest((EVALS / "rfqs" / gold["file"]).read_bytes(), gold["file"])
    draft = Pipeline(catalog, rules, oracle_llm(gold, catalog)).run(doc, date.fromisoformat(gold["received"]), reply=False)
    return gold, draft


@pytest.mark.parametrize("path", GOLD, ids=lambda p: p.stem)
def test_read_as_the_gold_says_every_line_prices_exactly_as_the_gold(path, catalog, rules):
    gold, draft = run_oracle(path, catalog, rules)
    h = draft.header
    assert (h.customer.customer_id, h.incoterm, h.destination_country, h.currency) == (
        gold["customer_id"], gold["incoterm"], gold["destination"], gold["currency"])
    assert sorted(f.code.value for f in draft.quote_flags) == gold["quote_flags"]
    assert len(draft.lines) == len(gold["lines"])
    for ln, g in zip(draft.lines, gold["lines"], strict=True):
        e, p = g["expected"], ln.priced
        where = f"line {g['line_no']} {g['mention']!r}"
        assert ln.match.sku == e["sku"], where
        assert sorted(f.code.value for f in ln.flags) == g["flags"], where
        if e["unit_price"] is None:
            assert p is None or p.unit_price is None, where
            continue
        assert ln.quantity.value == Decimal(e["quantity_uom"]), where
        assert (p.pack_code, p.packs, p.price_row_id, p.freight_row_id) == (
            e["pack_code"], e["packs"], e["price_row_id"], e["freight_row_id"]), where
        assert (p.unit_price, p.line_total) == (Decimal(e["unit_price"]), Decimal(e["line_total"])), where
    assert draft.total == (Decimal(gold["total"]) if gold["total"] else None)
    assert draft.needs_human == any(g["needs_human"] for g in gold["lines"]) or bool(
        {f for f in draft.quote_flags if f.severity == "block"})


# ------------------------------------------------------------------------------------------------ reading checks

RFQ = """From: Jijo Rawat <jijo.rawat@westbrookadhesives.com>
Please quote CIF Houston:
  Ethyl acetate, technical - 18 drums
Regards, Jijo"""


def scripted(lines, incoterms=("CIF",), match=None):
    def extraction(_s, _u):
        return {"buyer_company": {"value": None, "span": None},
                "incoterms": [{"term": t, "place": None, "span": f"{t} Houston"} for t in incoterms],
                "destination_country": {"value": "US", "span": "Houston"},
                "requested_date": {"value": None, "span": None}, "currency": {"value": None, "span": None},
                "document_grade": {"value": None, "span": None}, "lines": lines}
    answers = {"rfq_extraction": extraction}
    if match:
        answers["catalog_match"] = match
    return ScriptedLLM(answers)


def line(mention, qty="18", unit="drums", grade="technical", span_qty=None, confidence=0.95, english=None):
    def sp(v, s=None):
        return {"value": v, "span": s if s is not None else v}
    return {"mention": sp(mention), "english_name": english, "cas": sp(None), "grade": sp(grade),
            "quantity": sp(qty, span_qty), "unit": sp(unit), "packaging": sp(None), "confidence": confidence, "note": None}


def run(llm, catalog, rules, text=RFQ):
    return Pipeline(catalog, rules, llm).run(ingest_text(text), date(2026, 10, 6), reply=False)


def codes(ln):
    return {f.code for f in ln.flags}


def test_a_quantity_not_in_the_document_sends_the_line_to_a_person(catalog, rules):
    draft = run(scripted([line("Ethyl acetate", qty="180", span_qty="18 drums")]), catalog, rules)
    assert FlagCode.LOW_CONFIDENCE in codes(draft.lines[0]) and draft.lines[0].needs_human


def test_a_product_name_not_in_the_document_sends_the_line_to_a_person(catalog, rules):
    draft = run(scripted([line("Butyl acetate")]), catalog, rules)
    assert FlagCode.LOW_CONFIDENCE in codes(draft.lines[0])


def test_low_reader_confidence_is_flagged(catalog, rules):
    draft = run(scripted([line("Ethyl acetate", confidence=0.4)]), catalog, rules)
    assert FlagCode.LOW_CONFIDENCE in codes(draft.lines[0])


def test_a_clean_line_matches_by_synonym_and_prices(catalog, rules):
    draft = run(scripted([line("Ethyl acetate")]), catalog, rules)
    ln = draft.lines[0]
    assert ln.match.method == "synonym" and ln.match.sku == "QX-ETAC-TG"
    assert ln.priced.packs == 18 and ln.priced.pack_code == "DRUM200"
    assert not ln.needs_human and draft.header.customer.customer_id == "C-023"


def test_the_model_may_only_choose_among_the_candidates(catalog, rules):
    text = RFQ.replace("Ethyl acetate", "Ethylacetat Spezial")
    llm = scripted([line("Ethylacetat Spezial")],
                   match=lambda _s, u: {"decisions": [{"line": 1, "substance": "ethyl acetate", "choice": "MEG",
                                                       "reason": "guess"}]})
    draft = run(llm, catalog, rules, text)
    ln = draft.lines[0]
    assert ln.match.sku is None and ln.priced is None
    assert "MEG" not in {c.chemical_code for c in ln.match.candidates}
    assert FlagCode.AMBIGUOUS_MATCH in codes(ln)


def test_a_model_choice_among_the_candidates_is_used_and_labelled(catalog, rules):
    text = RFQ.replace("Ethyl acetate", "Ethylacetat Spezial")
    llm = scripted([line("Ethylacetat Spezial")],
                   match=lambda _s, u: {"decisions": [{"line": 1, "substance": "Ethyl acetate", "choice": "ETAC",
                                                       "reason": "German name"}]})
    ln = run(llm, catalog, rules, text).lines[0]
    assert ln.match.method == "llm" and ln.match.sku == "QX-ETAC-TG" and ln.priced.priced


def test_a_model_choice_that_is_not_the_substance_it_named_is_discarded(catalog, rules):
    text = RFQ.replace("Ethyl acetate", "Cellosolve acetate")
    llm = scripted([line("Cellosolve acetate")],
                   match=lambda _s, u: {"decisions": [{"line": 1, "substance": "2-ethoxyethyl acetate", "choice": "ETAC",
                                                       "reason": "closest acetate"}]})
    ln = run(llm, catalog, rules, text).lines[0]
    assert ln.match.sku is None and ln.priced is None and ln.needs_human
    assert FlagCode.AMBIGUOUS_MATCH in codes(ln)


def test_a_cas_number_not_in_the_document_is_not_used_to_match(catalog, rules):
    read = line("Ethyl acetate") | {"cas": {"value": "108-65-6", "span": "108-65-6"}}  # PGMEA's CAS, not in the RFQ
    ln = run(scripted([read]), catalog, rules).lines[0]
    assert ln.match.method == "synonym" and ln.match.sku == "QX-ETAC-TG"


def test_a_quantity_may_come_as_a_json_number(catalog, rules):
    ln = run(scripted([line("Ethyl acetate") | {"quantity": {"value": 18, "span": "18"}}]), catalog, rules).lines[0]
    assert ln.extracted.quantity.verified and ln.priced.packs == 18


def test_a_pack_size_alone_is_not_an_amount(catalog, rules):
    text = RFQ.replace("18 drums", "in 25 L cans")
    ln = run(scripted([line("Ethyl acetate", qty="25", unit="L cans")]), catalog, rules, text).lines[0]
    assert FlagCode.QUANTITY_MISSING in codes(ln) and ln.needs_human and ln.quantity.value is None


def test_a_count_of_packs_is_copied_and_code_works_out_the_amount(catalog, rules):
    text = RFQ.replace("18 drums", "20 x 25L cans")
    ok = run(scripted([line("Ethyl acetate", qty="20", unit="x 25L")]), catalog, rules, text).lines[0]
    assert ok.quantity.steps[0] == "20 x 25 L jerrycan = 500 L"
    multiplied = run(scripted([line("Ethyl acetate", qty="500", unit="L", span_qty="20 x 25L")]), catalog, rules, text)
    assert FlagCode.LOW_CONFIDENCE in codes(multiplied.lines[0])  # 500 is not written anywhere


def test_the_fob_place_is_not_the_destination(catalog, rules):
    text = RFQ.replace("CIF Houston", "FOB Nhava Sheva")

    def extraction(_s, _u):
        return {"buyer_company": {"value": None, "span": None},
                "incoterms": [{"term": "FOB", "place": "Nhava Sheva", "span": "FOB Nhava Sheva"}],
                "destination_country": {"value": "IN", "span": "Nhava Sheva"},
                "requested_date": {"value": None, "span": None}, "currency": {"value": None, "span": None},
                "document_grade": {"value": None, "span": None}, "lines": [line("Ethyl acetate")]}
    h = run(ScriptedLLM({"rfq_extraction": extraction}), catalog, rules, text).header
    assert h.destination_country == "US" and h.destination_source == "customer"


@pytest.mark.parametrize(("text", "after", "end"), [
    ("Liefertermin KW 48", date(2026, 10, 9), date(2026, 11, 29)),
    ("week 12/2027", date(2026, 10, 9), date(2027, 3, 28)),
    ("CW2", date(2026, 10, 9), date(2027, 1, 17)),  # week 2 of 2026 has passed
    ("delivery weekly", date(2026, 10, 9), None),
])
def test_a_week_number_is_dated_by_code(text, after, end):
    assert week_end(text, after) == end


def test_an_english_name_from_the_reader_matches_as_a_translation(catalog, rules):
    text = RFQ.replace("Ethyl acetate", "Essigsäureethylester")
    llm = scripted([line("Essigsäureethylester", english="ethyl acetate")])
    ln = run(llm, catalog, rules, text).lines[0]
    assert ln.match.method == "translation" and ln.match.sku == "QX-ETAC-TG"


def test_an_english_name_containing_a_catalog_name_is_not_that_product(catalog, rules):
    text = RFQ.replace("Ethyl acetate", "Cellosolve acetate (EGEEA)")
    llm = scripted([line("Cellosolve acetate (EGEEA)", english="ethylene glycol ethyl ether acetate")],
                   match=lambda _s, u: {"decisions": [{"line": 1, "substance": "2-ethoxyethyl acetate", "choice": "none",
                                                       "reason": "not carried"}]})
    ln = run(llm, catalog, rules, text).lines[0]
    assert ln.match.method == "none" and ln.match.sku is None and ln.needs_human  # not MEG


def test_two_incoterms_price_the_customers_usual_one_and_block(catalog, rules):
    text = RFQ.replace("CIF Houston", "CIF Houston, or FOB Houston")
    draft = run(scripted([line("Ethyl acetate")], incoterms=("CIF", "FOB")), catalog, rules, text)
    assert draft.header.incoterm == "CIF" and draft.header.incoterm_source == "conflict_customer_default"
    assert FlagCode.INCOTERM_CONFLICT in {f.code for f in draft.quote_flags} and draft.needs_human


def test_an_unverified_incoterm_is_ignored(catalog, rules):
    draft = run(scripted([line("Ethyl acetate")], incoterms=("DAP",)), catalog, rules)
    assert draft.header.incoterms_mentioned == () and draft.header.incoterm_source == "customer_default"


# ------------------------------------------------------------------------------------------------ edits


def test_choosing_a_sku_settles_no_match_and_prices_the_line(catalog, rules):
    text = RFQ.replace("Ethyl acetate", "Ethylacetat Spezial")
    llm = scripted([line("Ethylacetat Spezial")], match=lambda _s, u: {"decisions": [{"line": 1, "choice": "none",
                                                                                         "reason": "unclear"}]})
    p = Pipeline(catalog, rules, llm)
    draft = p.run(ingest_text(text), date(2026, 10, 6), reply=False)
    assert FlagCode.NO_MATCH in codes(draft.lines[0]) and draft.lines[0].priced is None
    fixed = p.reprice(draft, Edits(lines=(LineEdit(line_no=1, sku="QX-ETAC-TG"),)))
    ln = fixed.lines[0]
    assert ln.match.method == "manual" and ln.priced.priced and FlagCode.NO_MATCH not in codes(ln)
    assert ln.priced.packs == 18 and fixed.total == ln.priced.line_total


def test_approval_clears_needs_human_and_an_edit_reopens_it(catalog, rules):
    p = Pipeline(catalog, rules, scripted([line("Ethyl acetate", qty="1", unit="drums")]))
    draft = p.run(ingest_text(RFQ.replace("18 drums", "1 drums")), date(2026, 10, 6), reply=False)
    assert FlagCode.BELOW_MOQ in codes(draft.lines[0]) and draft.needs_human
    ok = p.reprice(draft, Edits(lines=(LineEdit(line_no=1, approved=True),)))
    assert not ok.needs_human and FlagCode.BELOW_MOQ in codes(ok.lines[0])  # still shown, accepted by a person
    again = p.reprice(ok, Edits(lines=(LineEdit(line_no=1, quantity=Decimal("100")),)))  # still one drum, below MOQ
    assert not again.lines[0].approved and again.lines[0].needs_human


def test_changing_the_incoterm_settles_the_conflict_and_reprices(catalog, rules):
    text = RFQ.replace("CIF Houston", "CIF Houston, or FOB Houston")
    p = Pipeline(catalog, rules, scripted([line("Ethyl acetate")], incoterms=("CIF", "FOB")))
    draft = p.run(ingest_text(text), date(2026, 10, 6), reply=False)
    fob = p.reprice(draft, Edits(incoterm="FOB"))
    assert fob.header.incoterm == "FOB" and not fob.quote_flags and not fob.needs_human
    assert fob.lines[0].priced.unit_price < draft.lines[0].priced.unit_price


def test_removing_and_adding_lines(catalog, rules):
    p = Pipeline(catalog, rules, scripted([line("Ethyl acetate")]))
    draft = p.run(ingest_text(RFQ), date(2026, 10, 6), reply=False)
    out = p.reprice(draft, Edits(lines=(LineEdit(line_no=1, remove=True),
                                        LineEdit(sku="QX-MEK-TG", quantity=Decimal("2000")))))
    assert [ln.line_no for ln in out.lines] == [2] and out.lines[0].match.method == "manual"
    assert out.total == out.lines[0].priced.line_total


# ------------------------------------------------------------------------------------------------ the email


def oracle_draft(catalog, rules, rfq="rfq-001"):
    return run_oracle(EVALS / "gold" / f"{rfq}.json", catalog, rules)[1]


def test_an_email_with_an_invented_number_is_replaced_by_the_template(catalog, rules):
    draft = oracle_draft(catalog, rules)
    facts = reply_facts(draft, catalog)
    good = "\n".join(f"{ln['unit_price']} {ln['line_total']}" for ln in facts["lines"]) + "\nTotal " + facts["total_of_priced_lines"]
    assert check_numbers("Re: quote", good, facts) == []
    assert any("3%" in p or "'3'" in p for p in check_numbers("Re: quote", good + " We can offer 3% off.", facts))
    llm = ScriptedLLM({"reply_email": lambda _s, _u: {"subject": "Quote", "body": good + "\nDelivery in 5 days."}})
    email, _ = draft_email(draft, catalog, llm)
    assert email.source == "template" and "rejected" in email.check


def test_an_email_that_drops_a_price_is_rejected(catalog, rules):
    draft = oracle_draft(catalog, rules)
    facts = reply_facts(draft, catalog)
    assert any("missing" in p for p in check_numbers("Re: quote", "Thank you, we will revert.", facts))


def test_the_template_states_every_price_and_passes_its_own_check(catalog, rules):
    from quotient.pipeline.output import template_email
    for rfq in ("rfq-001", "rfq-008", "rfq-010", "rfq-011", "rfq-017"):
        draft = oracle_draft(catalog, rules, rfq)
        facts = reply_facts(draft, catalog)
        subject, body = template_email(facts)
        assert check_numbers(subject, body, facts) == [], rfq


def test_lines_that_need_a_person_carry_no_price_in_the_email(catalog, rules):
    draft = oracle_draft(catalog, rules, "rfq-008")  # sodium hypochlorite is not in the range
    held = [ln for ln in reply_facts(draft, catalog)["lines"] if "unit_price" not in ln]
    assert held and all(ln["status"] == "to be confirmed separately" for ln in held)
