"""The scorer must catch what a wrong reading costs, not just reward a right one."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from evals.oracle import oracle_llm
from evals.run import score_rfq, summarise
from quotient.llm import ScriptedLLM
from quotient.pipeline.extract import numbers_in
from quotient.pipeline.ingest import ingest
from quotient.pipeline.run import Pipeline

EVALS = Path(__file__).resolve().parents[1] / "evals"


def gold(rfq: str) -> dict:
    return json.loads((EVALS / "gold" / f"{rfq}.json").read_text())


def drafted(g: dict, llm, catalog, rules):
    doc = ingest((EVALS / "rfqs" / g["file"]).read_bytes(), g["file"])
    return Pipeline(catalog, rules, llm).run(doc, date.fromisoformat(g["received"]), reply=False)


def misreading(g: dict, catalog, **line1) -> ScriptedLLM:
    """The oracle, with line 1 read differently."""
    oracle = oracle_llm(g, catalog)

    def extraction(system, user):
        raw = oracle.answers["rfq_extraction"](system, user)
        for k, v in line1.items():
            raw["lines"][0][k]["value"] = v
        return raw

    return ScriptedLLM({**oracle.answers, "rfq_extraction": extraction})


def test_a_plausible_wrong_quantity_is_a_silent_error(catalog, rules):
    g = gold("rfq-001")  # line 1 is 16 drums; "8" also occurs in the document, so the reading verifies
    draft = drafted(g, misreading(g, catalog, quantity="8"), catalog, rules)
    rec = score_rfq(g, draft, catalog)
    first = rec["lines"][0]
    assert not first["fields"]["quantity_written"] and first["price"]["exact"] is False
    assert first["silent_error"] and not rec["total"]["exact"]
    s = summarise([rec])
    assert s["routing"]["silent_errors"] == 1 and s["price"]["lines"]["correct"] == s["price"]["lines"]["total"] - 1


def test_an_unverifiable_quantity_goes_to_a_person_and_is_not_silent(catalog, rules):
    g = gold("rfq-001")
    draft = drafted(g, misreading(g, catalog, quantity="17"), catalog, rules)
    first = score_rfq(g, draft, catalog)["lines"][0]
    assert first["price"]["exact"] is False and first["needs_human"]["pred"] and not first["silent_error"]


def test_a_failed_rfq_scores_as_all_wrong(catalog):
    g = gold("rfq-005")
    rec = score_rfq(g, None, catalog, error="LLMError: boom")
    s = summarise([rec])
    assert s["failed"] == 1 and s["match"]["overall"]["correct"] == 0
    assert s["routing"]["silent_errors"] == len(g["lines"]) and s["flags"]["recall"] == 0


def test_every_gold_quantity_is_written_in_its_document():
    """The reader must copy numbers, so the gold may not hold one it would have to compute ("20 x 25L" is 20)."""
    for path in sorted((EVALS / "gold").glob("rfq-*.json")):
        g = json.loads(path.read_text())
        doc = ingest((EVALS / "rfqs" / g["file"]).read_bytes(), g["file"])
        written = numbers_in(doc.text)
        for ln in g["lines"]:
            assert ln["quantity"] is None or Decimal(ln["quantity"]) in written, f"{path.stem} line {ln['line_no']}"
