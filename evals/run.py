"""Run the eval set through the pipeline and score every RFQ against its gold answer.

    uv run python evals/run.py                                   # the model in .env, all 30 RFQs
    uv run python evals/run.py --oracle                          # a scripted perfect reader: the logic's ceiling
    uv run python evals/run.py --model openai/gpt-oss-20b --split holdout

Results go to evals/results/<timestamp>-<model>.json with the dev and holdout splits reported separately. The
holdout ten were fixed before any prompt was written and are never used to tune one.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import subprocess
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from rapidfuzz import fuzz

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.oracle import oracle_llm  # noqa: E402
from quotient import tracing  # noqa: E402
from quotient.catalog import Catalog, default_catalog  # noqa: E402
from quotient.config import settings  # noqa: E402
from quotient.domain import SEVERITY, FlagCode, Severity  # noqa: E402
from quotient.grades import canonical_grade  # noqa: E402
from quotient.llm import PRICES_CHECKED, GroqLLM  # noqa: E402
from quotient.pipeline.extract import norm  # noqa: E402
from quotient.pipeline.ingest import ingest  # noqa: E402
from quotient.pipeline.match import find_cas  # noqa: E402
from quotient.pipeline.run import Pipeline  # noqa: E402
from quotient.pipeline.types import LineResult, QuoteDraft  # noqa: E402
from quotient.pricing.rules import default_rules  # noqa: E402
from quotient.pricing.units import canonical_unit, pack_for_word, sized_pack  # noqa: E402

EVALS = ROOT / "evals"
RESULTS = EVALS / "results"
LINE_FIELDS = ("line_found", "quantity_written", "unit_written", "grade_stated", "cas", "quantity_in_uom", "pack")
HEADER_FIELDS = ("customer", "incoterms_mentioned", "incoterm", "destination", "currency", "requested_date")


# ------------------------------------------------------------------------------------------------ alignment


def align(gold_lines: list[dict], pred: tuple[LineResult, ...]) -> dict[int, LineResult]:
    """Pair each gold line with at most one predicted line: same SKU first, then the closest wording."""
    pairs = []
    for gi, g in enumerate(gold_lines):
        for p in pred:
            wording = p.extracted.mention.value if p.extracted and p.extracted.mention.value else ""
            score = fuzz.token_set_ratio(norm(g["mention"]), norm(wording))
            if g["sku"] and p.match.sku == g["sku"]:
                score += 100
            pairs.append((score, gi, p.line_no, p))
    pairs.sort(key=lambda x: (-x[0], x[1], x[2]))
    out, used = {}, set()
    for score, gi, pno, p in pairs:
        if score >= 60 and gi not in out and pno not in used:
            out[gi] = p
            used.add(pno)
    return out


def _pack(text: str, form: str, packs: dict) -> str | None:
    """The pack a unit names, by size ("x 25L", "25L cans") or by word ("cans")."""
    sized = sized_pack(text, form, packs)
    return sized[2] if sized else pack_for_word(text, form)


def _same_unit(pred: str | None, gold: str | None, form: str | None, packs: dict) -> bool:
    if pred is None or gold is None:
        return pred is None and gold is None
    if canonical_unit(pred) == canonical_unit(gold):
        return True
    if form:
        a, b = _pack(pred, form, packs), _pack(gold, form, packs)
        if a or b:
            return a == b
    return norm(pred) == norm(gold)


def _dec(v) -> Decimal | None:
    try:
        return None if v is None else Decimal(str(v))
    except ArithmeticError:
        return None


# ------------------------------------------------------------------------------------------------ one RFQ


def score_rfq(gold: dict, draft: QuoteDraft | None, catalog: Catalog, error: str | None = None) -> dict:
    gl = gold["lines"]
    pairs = align(gl, draft.lines) if draft else {}
    lines = []
    for gi, g in enumerate(gl):
        e = g["expected"]
        p = pairs.get(gi)
        x = p.extracted if p else None
        form = catalog.product(g["sku"]).form if g["sku"] else None
        fields: dict[str, bool | None] = {"line_found": p is not None}
        fields["quantity_written"] = x is not None and _dec(x.quantity.value) == _dec(g["quantity"])
        fields["unit_written"] = x is not None and _same_unit(x.unit.value, g["unit"], form, catalog.packs)
        stated = p.match.grade if p and p.match.grade_source in ("line", "document") else None
        fields["grade_stated"] = p is not None and (stated if p.match.sku else canonical_grade(x.grade.value if x else None)) == g["grade"]
        fields["cas"] = None if not g["cas"] else x is not None and g["cas"] in find_cas(f"{x.cas.value or ''} {x.mention.value or ''}")
        fields["quantity_in_uom"] = None if e["quantity_uom"] is None else p is not None and p.quantity.value == _dec(e["quantity_uom"])
        priced = p.priced if p else None
        fields["pack"] = None if e["pack_code"] is None else priced is not None and priced.pack_code == e["pack_code"]

        sku_ok = p is not None and p.match.sku == g["sku"]
        exact = None
        if e["unit_price"] is not None:
            exact = bool(priced and priced.unit_price == _dec(e["unit_price"]) and priced.line_total == _dec(e["line_total"]))
        pred_flags = sorted(f.code.value for f in p.flags) if p else []
        pred_human = p.needs_human if p else None
        wrong = not sku_ok or exact is False or (e["unit_price"] is None and priced is not None and priced.priced)
        lines.append({
            "line_no": g["line_no"], "mention": g["mention"], "pred_line": p.line_no if p else None,
            "pred_mention": x.mention.value if x else None, "fields": fields,
            "sku": {"gold": g["sku"], "pred": p.match.sku if p else None, "method": p.match.method if p else "missed",
                    "ok": sku_ok},
            "price": {"gold_unit": e["unit_price"], "pred_unit": str(priced.unit_price) if priced and priced.unit_price is not None else None,
                      "gold_total": e["line_total"], "pred_total": str(priced.line_total) if priced and priced.line_total is not None else None,
                      "exact": exact},
            "flags": {"gold": g["flags"], "pred": pred_flags},
            # line level: the line's own blocking flags (a quote-level flag is scored on the quote)
            "needs_human": {"gold": any(SEVERITY[FlagCode(c)] == Severity.BLOCK for c in g["flags"]), "pred": pred_human},
            # wrong and not sent to a person: the error a reviewer would not be prompted to catch
            "silent_error": bool(p is None or (wrong and not p.needs_human)),
        })
    paired = {p.line_no for p in pairs.values()}
    extra = [{"line_no": p.line_no, "mention": p.extracted.mention.value if p.extracted else None,
              "sku": p.match.sku, "flags": sorted(f.code.value for f in p.flags), "needs_human": p.needs_human,
              "silent_error": not p.needs_human}
             for p in (draft.lines if draft else ()) if p.line_no not in paired]

    header = {k: None for k in HEADER_FIELDS}
    if draft:
        h = draft.header
        header = {"customer": h.customer.customer_id == gold["customer_id"],
                  "incoterms_mentioned": sorted(h.incoterms_mentioned) == sorted(gold["incoterms_mentioned"]),
                  "incoterm": h.incoterm == gold["incoterm"], "destination": h.destination_country == gold["destination"],
                  "currency": h.currency == gold["currency"],
                  "requested_date": (str(h.requested_date) if h.requested_date else None) == gold["requested_date"]}
    return {
        "id": gold["id"], "format": gold["format"], "error": error, "lines": lines, "extra_lines": extra, "header": header,
        "quote_flags": {"gold": gold["quote_flags"], "pred": sorted(f.code.value for f in draft.quote_flags) if draft else []},
        "total": {"gold": gold["total"], "pred": str(draft.total) if draft and draft.total is not None else None,
                  "exact": bool(draft) and _dec(gold["total"]) == draft.total},
        "needs_human": {"gold": any(g["needs_human"] for g in gl)
                        or any(SEVERITY[FlagCode(f)] == Severity.BLOCK for f in gold["quote_flags"]),
                        "pred": draft.needs_human if draft else None},
        "email": {"source": draft.email.source, "check": draft.email.check} if draft and draft.email else None,
        "timing": None, "llm": None,
    }


# ------------------------------------------------------------------------------------------------ summary


def _acc(c: int, t: int) -> dict:
    return {"correct": c, "total": t, "accuracy": round(c / t, 4) if t else None}


def _pr(tp: int, fp: int, fn: int) -> dict:
    return {"tp": tp, "fp": fp, "fn": fn, "recall": round(tp / (tp + fn), 4) if tp + fn else None,
            "precision": round(tp / (tp + fp), 4) if tp + fp else None}


def _p90(xs: list[float]) -> float | None:
    return sorted(xs)[math.ceil(0.9 * len(xs)) - 1] if xs else None


def _median(xs: list[float]) -> float | None:
    return statistics.median(xs) if xs else None


def summarise(records: list[dict]) -> dict:
    lines = [ln for r in records for ln in r["lines"]]
    extraction = {}
    for f in LINE_FIELDS:
        vals = [ln["fields"][f] for ln in lines if ln["fields"][f] is not None]
        extraction[f] = _acc(sum(vals), len(vals))
    for f in HEADER_FIELDS:
        vals = [bool(r["header"][f]) for r in records]
        extraction[f] = _acc(sum(vals), len(vals))

    by_method: dict[str, list[bool]] = defaultdict(list)
    for ln in lines:
        by_method[ln["sku"]["method"]].append(ln["sku"]["ok"])
    match = {"overall": _acc(sum(ln["sku"]["ok"] for ln in lines), len(lines)),
             "by_method": {m: _acc(sum(v), len(v)) for m, v in sorted(by_method.items())},
             "extra_lines": sum(len(r["extra_lines"]) for r in records)}

    priced = [ln["price"]["exact"] for ln in lines if ln["price"]["exact"] is not None]
    price = {"lines": _acc(sum(priced), len(priced)), "quote_totals": _acc(sum(r["total"]["exact"] for r in records), len(records))}

    per_code: dict[str, Counter] = defaultdict(Counter)
    for r in records:
        sets = [(set(ln["flags"]["gold"]), set(ln["flags"]["pred"])) for ln in r["lines"]]
        sets += [(set(), set(x["flags"])) for x in r["extra_lines"]]
        sets.append((set(r["quote_flags"]["gold"]), set(r["quote_flags"]["pred"])))
        for gold, pred in sets:
            for c in gold & pred:
                per_code[c]["tp"] += 1
            for c in pred - gold:
                per_code[c]["fp"] += 1
            for c in gold - pred:
                per_code[c]["fn"] += 1
    tot = Counter()
    for c in per_code.values():
        tot.update(c)
    flags = {**_pr(tot["tp"], tot["fp"], tot["fn"]),
             "by_code": {c: _pr(v["tp"], v["fp"], v["fn"]) for c, v in sorted(per_code.items())}}

    gold_h = [ln["needs_human"]["gold"] for ln in lines]
    pred_h = [bool(ln["needs_human"]["pred"]) for ln in lines]
    tp = sum(g and p for g, p in zip(gold_h, pred_h, strict=True))
    silent = [f"{r['id']} line {ln['line_no']} ({ln['mention']})" for r in records for ln in r["lines"] if ln["silent_error"]]
    silent += [f"{r['id']} extra line {x['line_no']} ({x['mention']})" for r in records for x in r["extra_lines"] if x["silent_error"]]
    auto = [ln for r in records for ln in r["lines"] if ln["needs_human"]["pred"] is False]
    routing = {"lines_needing_a_person": _pr(tp, sum(p and not g for g, p in zip(gold_h, pred_h, strict=True)),
                                             sum(g and not p for g, p in zip(gold_h, pred_h, strict=True))),
               "auto_quoted_lines": _acc(sum(not ln["silent_error"] for ln in auto), len(auto)),
               "silent_errors": len(silent), "silent_error_lines": silent}

    ok = [r for r in records if r["timing"]]
    wall = [r["timing"]["wall_s"] for r in ok]
    active = [r["timing"]["active_s"] for r in ok]
    stages: dict[str, list[float]] = defaultdict(list)
    for r in ok:
        for s, v in r["timing"]["stages"].items():
            stages[s].append(v)
    timing = {"median_s": _median(wall), "p90_s": _p90(wall), "median_active_s": _median(active),
              "p90_active_s": _p90(active), "stage_median_s": {s: round(_median(v), 3) for s, v in stages.items()}}
    costs = [Decimal(r["llm"]["cost_usd"]) for r in ok]
    prompt = sum(r["llm"]["prompt_tokens"] for r in ok)
    cost = {"mean_usd": str(round(sum(costs) / len(costs), 6)) if costs else None,
            "median_usd": str(round(statistics.median(costs), 6)) if costs else None,
            "p90_usd": str(round(_p90(costs), 6)) if costs else None, "total_usd": str(round(sum(costs), 6)),
            "calls_per_rfq": round(sum(r["llm"]["calls"] for r in ok) / len(ok), 2) if ok else None,
            "prompt_tokens": prompt, "cached_share": round(sum(r["llm"]["cached_tokens"] for r in ok) / prompt, 4) if prompt else None,
            "completion_tokens": sum(r["llm"]["completion_tokens"] for r in ok),
            "reasoning_tokens": sum(r["llm"]["reasoning_tokens"] for r in ok)}
    email = Counter(r["email"]["source"] for r in records if r["email"])
    return {"rfqs": len(records), "failed": sum(1 for r in records if r["error"]), "lines": len(lines),
            "extraction": extraction, "match": match, "price": price, "flags": flags, "routing": routing,
            "time": timing, "cost": cost, "email": dict(email)}


# ------------------------------------------------------------------------------------------------ running


def run_one(gold: dict, pipeline_for, catalog: Catalog, reply: bool) -> tuple[dict, QuoteDraft | None]:
    data = (EVALS / "rfqs" / gold["file"]).read_bytes()
    started = time.perf_counter()
    try:
        doc = ingest(data, gold["file"])
        draft = pipeline_for(gold).run(doc, date.fromisoformat(gold["received"]), reply=reply)
    except Exception as exc:  # a failed RFQ is scored as all wrong, never skipped
        return score_rfq(gold, None, catalog, error=f"{type(exc).__name__}: {exc}"), None
    wall = time.perf_counter() - started
    rec = score_rfq(gold, draft, catalog)
    rec["timing"] = {"wall_s": round(wall, 3), "active_s": round(wall - draft.llm.waited_seconds, 3),
                     "waited_s": draft.llm.waited_seconds, "stages": {t.stage: t.seconds for t in draft.timings}}
    rec["llm"] = json.loads(draft.llm.model_dump_json())
    return rec, draft


def _git() -> str:
    try:
        head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--", "quotient", "evals/specs", "data"], cwd=ROOT,
                               capture_output=True, text=True).stdout.strip()
        return head + ("-dirty" if dirty else "")
    except OSError:
        return "unknown"


def fmt_pct(a: dict) -> str:
    return "  -  " if a["accuracy"] is None else f"{a['accuracy'] * 100:5.1f}% ({a['correct']}/{a['total']})"


def report(splits: dict[str, dict]) -> str:
    names = list(splits)
    rows = [("", *names)]

    def add(label, fn):
        rows.append((label, *[fn(splits[n]) for n in names]))

    for f in (*LINE_FIELDS, *HEADER_FIELDS):
        add(f"extract: {f}", lambda s, f=f: fmt_pct(s["extraction"][f]))
    add("match: SKU overall", lambda s: fmt_pct(s["match"]["overall"]))
    for m in sorted({m for s in splits.values() for m in s["match"]["by_method"]}):
        add(f"  by method: {m}", lambda s, m=m: fmt_pct(s["match"]["by_method"][m]) if m in s["match"]["by_method"] else "  -  ")
    add("price: line exact", lambda s: fmt_pct(s["price"]["lines"]))
    add("price: quote total exact", lambda s: fmt_pct(s["price"]["quote_totals"]))
    add("flags: recall", lambda s: f"{(s['flags']['recall'] or 0) * 100:5.1f}%")
    add("flags: precision", lambda s: f"{(s['flags']['precision'] or 0) * 100:5.1f}%")
    add("routing: recall of lines needing a person",
        lambda s: f"{(s['routing']['lines_needing_a_person']['recall'] or 0) * 100:5.1f}%")
    add("routing: auto-quoted lines correct", lambda s: fmt_pct(s["routing"]["auto_quoted_lines"]))
    add("routing: silent errors", lambda s: str(s["routing"]["silent_errors"]))
    add("time: median / p90 s", lambda s: f"{s['time']['median_s'] or 0:.1f} / {s['time']['p90_s'] or 0:.1f}")
    add("time excl. rate-limit waits", lambda s: f"{s['time']['median_active_s'] or 0:.1f} / {s['time']['p90_active_s'] or 0:.1f}")
    add("cost: mean USD per RFQ", lambda s: s["cost"]["mean_usd"] or "-")
    add("failed RFQs", lambda s: str(s["failed"]))
    widths = [max(len(str(r[i])) for r in rows) for i in range(len(rows[0]))]
    return "\n".join("  ".join(str(c).ljust(w) for c, w in zip(r, widths, strict=True)) for r in rows)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", help="Groq model id (default: QUOTIENT_MODEL)")
    ap.add_argument("--effort", help="reasoning effort for gpt-oss models (default: QUOTIENT_REASONING_EFFORT)")
    ap.add_argument("--oracle", action="store_true", help="scripted perfect reader instead of a model")
    ap.add_argument("--split", choices=("all", "dev", "holdout"), default="all")
    ap.add_argument("--only", nargs="*", help="RFQ ids to run")
    ap.add_argument("--workers", type=int, default=1, help="RFQs in parallel (mind the rate limits)")
    ap.add_argument("--no-reply", action="store_true", help="skip the reply email")
    ap.add_argument("--no-save", action="store_true")
    args = ap.parse_args()

    catalog, rules = default_catalog(), default_rules()
    split = json.loads((EVALS / "split.json").read_text())
    ids = {"all": split["dev"] + split["holdout"], "dev": split["dev"], "holdout": split["holdout"]}[args.split]
    if args.only:
        ids = [i for i in ids if i in set(args.only)]
    golds = [json.loads((EVALS / "gold" / f"{i}.json").read_text()) for i in sorted(ids)]

    s = settings()
    if args.oracle:
        model, effort = "oracle", None

        def pipeline_for(gold):
            return Pipeline(catalog, rules, oracle_llm(gold, catalog))
    else:
        model, effort = args.model or s.quotient_model, args.effort or s.quotient_reasoning_effort
        llm = GroqLLM(s.groq_api_key, model, effort)
        shared = Pipeline(catalog, rules, llm)

        def pipeline_for(_gold):
            return shared

    print(f"running {len(golds)} RFQs with {model}" + (f" (reasoning {effort})" if effort else ""), file=sys.stderr)
    started = time.perf_counter()

    drafts: dict[str, QuoteDraft] = {}

    def one(g):
        rec, draft = run_one(g, pipeline_for, catalog, reply=not args.no_reply and not args.oracle)
        if draft is not None:
            drafts[g["id"]] = draft
        status = rec["error"] or f"{sum(ln['sku']['ok'] for ln in rec['lines'])}/{len(rec['lines'])} SKUs, " \
                                 f"{sum(1 for ln in rec['lines'] if ln['price']['exact'])} exact prices"
        print(f"  {g['id']}: {status}" + (f" [{rec['timing']['wall_s']:.1f}s]" if rec["timing"] else ""), file=sys.stderr)
        return rec

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        records = list(pool.map(one, golds))
    tracing.flush()

    holdout = set(split["holdout"])
    splits = {"dev": summarise([r for r in records if r["id"] not in holdout]),
              "holdout": summarise([r for r in records if r["id"] in holdout]),
              "all": summarise(records)}
    splits = {k: v for k, v in splits.items() if v["rfqs"]}
    print(report(splits))

    result = {
        "run": {"at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "model": model, "reasoning_effort": effort,
                "oracle": args.oracle, "prices_checked": None if args.oracle else PRICES_CHECKED, "code": _git(), "rules_version": rules.version, "data_version": catalog.version,
                "rfqs": len(records), "wall_s": round(time.perf_counter() - started, 1), "workers": args.workers,
                "holdout_ids": sorted(holdout)},
        "splits": splits,
        "rfqs": records,
    }
    if not args.no_save:
        RESULTS.mkdir(exist_ok=True)
        slug = model.replace("/", "_")
        out = RESULTS / f"{datetime.now(UTC):%Y%m%d-%H%M%S}-{slug}.json"
        out.write_text(json.dumps(result, indent=1, default=str) + "\n")
        print(f"\nsaved {out.relative_to(ROOT)}", file=sys.stderr)
        if not args.oracle:  # the latest live drafts per model: the demo replays these instead of spending a call
            ddir = RESULTS / "drafts" / slug
            ddir.mkdir(parents=True, exist_ok=True)
            for rid, d in drafts.items():
                (ddir / f"{rid}.json").write_text(json.dumps({"run": out.name, "model": model, "effort": effort,
                                                              "draft": d.model_dump(mode="json")}, indent=1) + "\n")


if __name__ == "__main__":
    main()
