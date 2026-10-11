"""A scripted model that reads every RFQ exactly as the gold answer says.

Running the pipeline with it isolates the deterministic stages (matching rules, grades, header rules, unit
conversion, pricing, flags) from the model: whatever differs from the gold is a logic error, not a reading
error. The eval runner reports it as the ceiling the live model is measured against.
"""

from __future__ import annotations

import re

from quotient.catalog import Catalog
from quotient.llm import ScriptedLLM


def oracle_llm(gold: dict, catalog: Catalog) -> ScriptedLLM:
    def extraction(_system: str, user: str) -> dict:
        doc = user.split("<document>\n", 1)[1].rsplit("\n</document>", 1)[0]

        def sp(value):  # the whole document as the span: the oracle's reading is right by definition
            return {"value": value, "span": doc if value is not None else None}

        lines = []
        for ln in gold["lines"]:
            pack = catalog.packs[ln["packaging"]].description if ln["packaging"] else None
            lines.append({"mention": sp(ln["mention"]), "english_name": None, "cas": sp(ln["cas"]),
                          "grade": sp(ln["grade"]), "quantity": sp(ln["quantity"]), "unit": sp(ln["unit"]),
                          "packaging": sp(pack), "confidence": 1.0, "note": None})
        return {"buyer_company": sp(None), "destination_country": sp(gold["destination"]),
                "incoterms": [{"term": t, "place": None, "span": doc} for t in gold["incoterms_mentioned"]],
                "requested_date": sp(gold["requested_date"]), "currency": sp(gold["currency"]),
                "document_grade": sp(None), "lines": lines}

    def match(_system: str, user: str) -> dict:
        decisions = []
        for no in re.findall(r"^Line (\d+): ", user, flags=re.M):
            sku = gold["lines"][int(no) - 1]["sku"]
            decisions.append({"line": int(no), "substance": catalog.product(sku).chemical_name if sku else "unknown",
                              "choice": catalog.product(sku).chemical_code if sku else "none", "reason": "gold answer"})
        return {"decisions": decisions}

    return ScriptedLLM({"rfq_extraction": extraction, "catalog_match": match}, model="oracle")
