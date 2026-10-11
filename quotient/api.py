"""HTTP API for the review screen and the Evals page.

    uv run uvicorn quotient.api:app --reload

Drafts are stateless: the client holds the draft and sends it back with each edit, and every response is
re-priced on the server, so a price shown or exported always comes from the engine, never from the client.
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections import defaultdict, deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel

from quotient import tracing
from quotient.config import ROOT, settings
from quotient.llm import LLMError, default_llm
from quotient.pipeline.ingest import IngestError, ingest, ingest_text
from quotient.pipeline.output import draft_email, quote_pdf
from quotient.pipeline.run import Edits, Pipeline
from quotient.pipeline.types import QuoteDraft
from quotient.pricing.engine import RULES_USED

EVALS = ROOT / "evals"
RESULTS = EVALS / "results"
MAX_UPLOAD = 5 * 1024 * 1024
LIVE_RUNS_PER_HOUR = 30

@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    yield
    tracing.flush()


app = FastAPI(title="Quotient", version="0.1.0", docs_url="/api/docs", openapi_url="/api/openapi.json", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in settings().quotient_cors_origins.split(",") if o.strip()],
                   allow_methods=["GET", "POST"], allow_headers=["*"], expose_headers=["X-Quotient-Replay"])


@lru_cache
def pipeline() -> Pipeline:
    s = settings()
    return Pipeline.default(default_llm() if s.groq_api_key else None)


# ------------------------------------------------------------------------------------------------ helpers

_COMPUTED = {"draft": ("needs_human",), "line": ("flags", "needs_human")}


def load_draft(raw: dict[str, Any]) -> QuoteDraft:
    """A draft the client sent back, minus the fields the server computes."""
    raw = {k: v for k, v in raw.items() if k not in _COMPUTED["draft"]}
    raw["lines"] = [{k: v for k, v in ln.items() if k not in _COMPUTED["line"]} for ln in raw.get("lines", [])]
    try:
        return QuoteDraft.model_validate(raw)
    except ValueError as exc:
        raise HTTPException(422, f"not a valid draft: {exc}") from exc


def as_json(draft: QuoteDraft, **headers: str) -> Response:
    return Response(draft.model_dump_json(), media_type="application/json", headers=headers)


class _Limiter:
    """Live model runs per client per hour, so a public demo cannot drain the API key."""

    def __init__(self, per_hour: int) -> None:
        self.per_hour, self.hits, self.lock = per_hour, defaultdict(deque), threading.Lock()

    def check(self, key: str) -> None:
        now = time.monotonic()
        with self.lock:
            q = self.hits[key]
            while q and now - q[0] > 3600:
                q.popleft()
            if len(q) >= self.per_hour:
                raise HTTPException(429, f"limit of {self.per_hour} live runs an hour reached; samples still replay")
            q.append(now)


limiter = _Limiter(LIVE_RUNS_PER_HOUR)


def _client(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "unknown")


def _split() -> dict[str, list[str]]:
    return json.loads((EVALS / "split.json").read_text())


def _replay(sample_id: str) -> tuple[QuoteDraft, str] | None:
    """The draft the configured model produced for a sample in the latest live eval run, if there is one."""
    path = RESULTS / "drafts" / settings().quotient_model.replace("/", "_") / f"{sample_id}.json"
    if not path.exists():
        return None
    saved = json.loads(path.read_text())
    return load_draft(saved["draft"]), saved["run"]


# ------------------------------------------------------------------------------------------------ routes


@app.get("/api/health")
def health() -> dict[str, Any]:
    p, s = pipeline(), settings()
    return {"ok": True, "model": p.llm.model if p.llm else None, "reasoning_effort": s.quotient_reasoning_effort,
            "live": p.llm is not None, "tracing": tracing.client() is not None, "rules_version": p.rules.version,
            "data_version": p.catalog.version, "fx_snapshot": str(p.catalog.fx_snapshot_date)}


@app.get("/api/catalog")
def catalog() -> dict[str, Any]:
    c = pipeline().catalog
    return {
        "products": [{"sku": p.sku, "name": p.name, "chemical": p.chemical_name, "cas": p.cas, "grade": p.grade,
                      "grade_label": p.grade_label, "uom": p.uom, "form": p.form, "packs": list(p.pack_codes),
                      "moq": str(p.moq), "hazard": p.un_number} for p in sorted(c.products.values(), key=lambda p: p.name)],
        "packs": {k: {"description": v.description, "size": str(v.size), "uom": v.size_uom, "form": v.form}
                  for k, v in c.packs.items()},
        "customers": [{"id": x.customer_id, "name": x.name, "country": x.country_code, "currency": x.currency,
                       "incoterm": x.default_incoterm, "tier": x.discount_tier} for x in c.customers.values()],
        "incoterms": pipeline().rules.param("FREIGHT-v1", "supported"),
        "currencies": sorted(c.fx),
        "countries": sorted({f.destination_country_code for f in c.freight.values()}),
    }


@app.get("/api/rules")
def rules() -> dict[str, Any]:
    r = pipeline().rules
    return {"name": r.name, "version": r.version, "engine": list(RULES_USED),
            "rules": {k: {"title": v.title, "text": v.text, "params": v.params} for k, v in r.rules.items()}}


_GREETING = re.compile(r"^(hi|hello|dear|good (morning|afternoon)|hallo|guten tag|to whom)\b|^(from|to|date|sent):", re.I)


def _title(text: str) -> str:
    """The first line of a document that says something (not a greeting or a mail header)."""
    lines = [ln.strip(" ,:") for ln in text.splitlines() if ln.strip()]
    return next((ln for ln in lines if not _GREETING.match(ln) and len(ln) > 12), lines[0] if lines else "")[:90]


@lru_cache
def _sample_index() -> tuple[dict[str, Any], ...]:
    holdout = set(_split()["holdout"])
    out = []
    for path in sorted((EVALS / "gold").glob("rfq-*.json")):
        g = json.loads(path.read_text())
        doc = ingest((EVALS / "rfqs" / g["file"]).read_bytes(), g["file"])
        out.append({"id": g["id"], "file": g["file"], "format": g["format"], "split": "holdout" if g["id"] in holdout else "dev",
                    "title": doc.subject or _title(doc.text), "lines": len(g["lines"]),
                    "sender": doc.sender_email, "received": g["received"]})
    return tuple(out)


@app.get("/api/samples")
def samples() -> list[dict[str, Any]]:
    model_dir = RESULTS / "drafts" / settings().quotient_model.replace("/", "_")
    return [{**s, "replay": (model_dir / f"{s['id']}.json").exists()} for s in _sample_index()]


@app.get("/api/samples/{sample_id}/file")
def sample_file(sample_id: str) -> FileResponse:
    matches = list((EVALS / "rfqs").glob(f"{sample_id}.*")) if sample_id.startswith("rfq-") and sample_id[4:].isdigit() else []
    if not matches:
        raise HTTPException(404, "no such sample")
    return FileResponse(matches[0], filename=matches[0].name)


@app.post("/api/quotes")
def create_quote(request: Request, file: Annotated[UploadFile | None, File()] = None,
                 text: Annotated[str | None, Form()] = None, sample: Annotated[str | None, Form()] = None,
                 live: Annotated[bool, Form()] = False) -> Response:
    """Read an RFQ (an uploaded file, pasted text or a sample) and return the priced draft."""
    p = pipeline()
    if sample:
        if not live and (hit := _replay(sample)):
            draft, run = hit
            return as_json(p.reprice(draft, Edits()), **{"X-Quotient-Replay": run})
        path = next((EVALS / "rfqs").glob(f"{sample}.*"), None) if sample.startswith("rfq-") else None
        if path is None:
            raise HTTPException(404, "no such sample")
        data, name = path.read_bytes(), path.name
    elif file is not None:
        data, name = file.file.read(MAX_UPLOAD + 1), file.filename or "upload"
        if len(data) > MAX_UPLOAD:
            raise HTTPException(413, "files up to 5 MB")
    elif text and text.strip():
        data, name = text.encode(), "pasted.txt"
    else:
        raise HTTPException(422, "send a file, pasted text or a sample id")

    if p.llm is None:
        raise HTTPException(503, "no model configured on the server (GROQ_API_KEY); samples with a stored run still open")
    try:
        doc = ingest_text(text, name) if name == "pasted.txt" else ingest(data, name)
    except IngestError as exc:
        raise HTTPException(422, str(exc)) from exc
    if sample:
        received = json.loads((EVALS / "gold" / f"{sample}.json").read_text())["received"]
        doc = doc.model_copy(update={"received": doc.received or date.fromisoformat(received)})
    limiter.check(_client(request))
    try:
        return as_json(p.run(doc))
    except LLMError as exc:
        raise HTTPException(502, f"the model call failed: {exc}") from exc


class DraftBody(BaseModel):
    draft: dict[str, Any]
    edits: Edits = Edits()


@app.post("/api/quotes/reprice")
def reprice(body: DraftBody) -> Response:
    try:
        return as_json(pipeline().reprice(load_draft(body.draft), body.edits))
    except (KeyError, ValueError) as exc:
        raise HTTPException(422, str(exc).strip("'\"")) from exc


@app.post("/api/quotes/email")
def email(body: DraftBody, request: Request) -> Response:
    p = pipeline()
    draft = p.reprice(load_draft(body.draft), body.edits)
    if p.llm is not None:
        limiter.check(_client(request))
    mail, _ = draft_email(draft, p.catalog, p.llm)
    return as_json(draft.model_copy(update={"email": mail}))


@app.post("/api/quotes/pdf")
def pdf(body: DraftBody) -> Response:
    p = pipeline()
    draft = p.reprice(load_draft(body.draft), body.edits)  # the PDF prints engine prices only
    return Response(quote_pdf(draft, p.catalog), media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{draft.id}.pdf"'})


@app.get("/api/evals")
def eval_runs() -> list[dict[str, Any]]:
    out = []
    for path in sorted(RESULTS.glob("*.json"), reverse=True):
        r = json.loads(path.read_text())
        out.append({"name": path.stem, "run": r["run"], "splits": r["splits"]})
    return out


@app.get("/api/evals/{name}")
def eval_run(name: str) -> JSONResponse:
    path = RESULTS / f"{Path(name).name}.json"
    if not path.exists():
        raise HTTPException(404, "no such eval run")
    return JSONResponse(json.loads(path.read_text()))
