"""The HTTP API, with the model replaced by the scripted oracle reader."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import quotient.api as api
from evals.oracle import oracle_llm
from quotient.pipeline.run import Pipeline

EVALS = Path(__file__).resolve().parents[1] / "evals"


def gold(rfq: str) -> dict:
    return json.loads((EVALS / "gold" / f"{rfq}.json").read_text())


@pytest.fixture
def client(monkeypatch, catalog, rules):
    def use(rfq: str | None):
        llm = oracle_llm(gold(rfq), catalog) if rfq else None
        p = Pipeline(catalog, rules, llm)
        monkeypatch.setattr(api, "pipeline", lambda: p)
        return p

    c = TestClient(api.app)
    c.use = use
    return c


def live(client, rfq: str) -> dict:
    client.use(rfq)
    r = client.post("/api/quotes", data={"sample": rfq, "live": "true"})
    assert r.status_code == 200, r.text
    return r.json()


def test_health_reports_versions(client):
    client.use(None)
    body = client.get("/api/health").json()
    assert body["live"] is False and body["rules_version"] and body["data_version"]


def test_samples_list_both_splits(client):
    client.use(None)
    rows = client.get("/api/samples").json()
    assert len(rows) == 30 and sum(r["split"] == "holdout" for r in rows) == 10


def test_a_sample_run_returns_the_draft_with_flags_and_routing(client):
    d = live(client, "rfq-008")
    assert d["total"] == gold("rfq-008")["total"]
    assert all("flags" in ln and "needs_human" in ln for ln in d["lines"]) and d["needs_human"] is True
    assert d["email"]["source"] == "template"  # the oracle writes no email; the template does


def test_reprice_recomputes_prices_the_client_sent(client):
    d = live(client, "rfq-001")
    true_price = d["lines"][0]["priced"]["unit_price"]
    d["lines"][0]["priced"]["unit_price"] = "0.001"  # tampered on the client
    d["total"] = "1.00"
    r = client.post("/api/quotes/reprice", json={"draft": d})
    assert r.status_code == 200
    assert r.json()["lines"][0]["priced"]["unit_price"] == true_price and r.json()["total"] == gold("rfq-001")["total"]


def test_approving_a_line_through_the_api(client):
    d = live(client, "rfq-019")  # Aminex T99: 4 drums is below the MOQ
    held = next(ln for ln in d["lines"] if ln["needs_human"])
    r = client.post("/api/quotes/reprice", json={"draft": d, "edits": {"lines": [{"line_no": held["line_no"], "approved": True}]}})
    after = next(ln for ln in r.json()["lines"] if ln["line_no"] == held["line_no"])
    assert not after["needs_human"] and after["approved"]


def test_pdf_prints_engine_prices(client):
    d = live(client, "rfq-001")
    d["lines"][0]["priced"]["line_total"] = "99999.99"
    r = client.post("/api/quotes/pdf", json={"draft": d})
    assert r.status_code == 200 and r.content.startswith(b"%PDF") and b"99999.99" not in r.content


def test_without_a_model_a_paste_is_refused_clearly(client):
    client.use(None)
    r = client.post("/api/quotes", data={"text": "Please quote 2 MT acetone CIF Hamburg"})
    assert r.status_code == 503 and "GROQ_API_KEY" in r.json()["detail"]


def test_a_sample_with_a_stored_live_run_replays_it(client, monkeypatch, tmp_path):
    d = live(client, "rfq-005")
    model_dir = tmp_path / "drafts" / api.settings().quotient_model.replace("/", "_")
    model_dir.mkdir(parents=True)
    (model_dir / "rfq-005.json").write_text(json.dumps({"run": "20261011-run.json", "draft": d}))
    monkeypatch.setattr(api, "RESULTS", tmp_path)
    client.use(None)  # no model: the replay needs none
    r = client.post("/api/quotes", data={"sample": "rfq-005"})
    assert r.status_code == 200 and r.headers["X-Quotient-Replay"] == "20261011-run.json"
    assert Decimal(r.json()["total"]) == Decimal(gold("rfq-005")["total"])


def test_a_broken_draft_is_a_422(client):
    client.use(None)
    assert client.post("/api/quotes/reprice", json={"draft": {"id": "x"}}).status_code == 422


def test_unsupported_upload_is_a_422(client):
    client.use("rfq-001")
    r = client.post("/api/quotes", files={"file": ("rfq.docx", b"PK\x03\x04", "application/octet-stream")})
    assert r.status_code == 422 and "unsupported" in r.json()["detail"]
