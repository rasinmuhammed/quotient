"""Deterministic matching on the gold readings: what the rules match alone, with no model."""

from __future__ import annotations

import json
from pathlib import Path

from quotient.pipeline.match import NameIndex, cas_valid, find_cas, match_lines, names_agree
from quotient.pipeline.types import ExtractedLine, Extraction, Spanned

GOLD = sorted((Path(__file__).resolve().parents[1] / "evals" / "gold").glob("rfq-*.json"))


def as_read(value: str | None) -> Spanned:
    return Spanned(value=value, span=value, verified=value is not None)


def test_rules_alone_match_73_of_80_gold_lines_and_none_wrong(catalog):
    right = wrong = deferred = 0
    for path in GOLD:
        lines = json.loads(path.read_text())["lines"]
        extraction = Extraction(
            buyer_company=as_read(None), incoterms=(), destination_country=as_read(None), requested_date=as_read(None),
            currency=as_read(None), document_grade=as_read(None),
            lines=tuple(ExtractedLine(mention=as_read(g["mention"]), english_name=None, cas=as_read(g["cas"]),
                                      grade=as_read(g["grade"]), quantity=as_read(g["quantity"]), unit=as_read(g["unit"]),
                                      packaging=as_read(None), confidence=1.0) for g in lines))
        matches, _, calls = match_lines(extraction, catalog, llm=None)  # no model: unmatched lines stay unmatched
        assert not calls
        for g, m in zip(lines, matches, strict=True):
            if m.method == "none":
                deferred += 1
            elif m.sku == g["sku"]:
                right += 1
            else:
                wrong += 1
    # The 7 left for the model: 4 products not in the range, 3 German spellings (Cyclohexanon, Essigsäure,
    # Tetrahydrofurane) that the reader's English name or the model's pick among candidates resolves.
    assert (right, wrong, deferred) == (73, 0, 7)


def test_cas_numbers_in_every_written_form():
    assert find_cas("CAS# 141-78-6") == ["141-78-6"]
    assert find_cas("CAS 123 86 4") == ["123-86-4"]
    assert find_cas("1330207") == ["1330-20-7"]
    assert find_cas("lot 1234567") == []  # a number whose check digit fails is not a CAS number
    assert cas_valid("7664-38-2") and not cas_valid("7664-38-3")


def test_a_longer_name_containing_a_catalog_name_is_not_matched_by_spelling(catalog):
    line = ExtractedLine(mention=as_read("DEG (diethylene glycol)"), english_name=None, cas=as_read(None),
                         grade=as_read(None), quantity=as_read("20"), unit=as_read("MT"), packaging=as_read(None),
                         confidence=1.0)
    extraction = Extraction(buyer_company=as_read(None), incoterms=(), destination_country=as_read(None),
                            requested_date=as_read(None), currency=as_read(None), document_grade=as_read(None),
                            lines=(line,))
    (m,), _, _ = match_lines(extraction, catalog, llm=None)
    assert m.method == "none" and m.sku is None  # never silently ethylene glycol


def test_a_model_pick_must_be_the_substance_the_model_named(catalog):
    idx = NameIndex.build(catalog)
    assert names_agree("Glacial acetic acid", "GAA", idx) and names_agree("acetic acid", "GAA", idx)
    assert names_agree("tetrahydrofuran", "THF", idx) and names_agree("Cyclohexanone", "CYC", idx)
    assert not names_agree("2-ethoxyethyl acetate", "ETAC", idx)  # shares a word, different ester
    assert not names_agree("diethylene glycol", "MEG", idx)  # contains "ethylene glycol", different glycol
    assert not names_agree("unknown", "ETAC", idx) and not names_agree("", "ETAC", idx)
