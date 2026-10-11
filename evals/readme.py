"""Write the eval tables into README.md from the saved results, so no number in the README is typed by hand.

    uv run python evals/readme.py              # newest live run of the default model, plus the oracle ceiling
    uv run python evals/readme.py --compare openai/gpt-oss-20b

Replaces the text between <!-- evals:start --> and <!-- evals:end -->.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from quotient.config import settings  # noqa: E402

RESULTS = ROOT / "evals" / "results"
README = ROOT / "README.md"
START, END = "<!-- evals:start -->", "<!-- evals:end -->"

FIELDS = {"line_found": "Line found", "quantity_written": "Quantity as written", "unit_written": "Unit as written",
          "grade_stated": "Grade stated", "cas": "CAS number", "quantity_in_uom": "Quantity in SKU unit", "pack": "Pack",
          "customer": "Customer", "incoterm": "Incoterm priced", "destination": "Destination", "currency": "Currency",
          "requested_date": "Requested date"}
METHODS = {"cas": "CAS number", "sku": "SKU code", "synonym": "Synonym table", "translation": "Reader's English name",
           "fuzzy": "Spelling match", "llm": "Model among 5 candidates", "none": "No match (correct when gold has none)",
           "missed": "Line not found"}


def latest(model: str | None, oracle: bool = False) -> dict | None:
    for path in sorted(RESULTS.glob("*.json"), reverse=True):
        r = json.loads(path.read_text())
        if r["run"]["oracle"] == oracle and (oracle or r["run"]["model"] == model) and r["run"]["rfqs"] == 30:
            return {**r, "file": path.name}
    return None


def pct(x: float | None, d: int = 1) -> str:
    return "–" if x is None else f"{x * 100:.{d}f}%"


def acc(a: dict | None) -> str:
    return "–" if not a or a["accuracy"] is None else f"{pct(a['accuracy'])} ({a['correct']}/{a['total']})"


def secs(x: float | None) -> str:
    return "–" if x is None else f"{x:.1f} s"


def headline(r: dict) -> list[str]:
    s = r["splits"]
    rows = [
        ("Right SKU, all gold lines", lambda x: acc(x["match"]["overall"])),
        ("Exact unit price and line total", lambda x: acc(x["price"]["lines"])),
        ("Exact quote total", lambda x: acc(x["price"]["quote_totals"])),
        ("Flag recall", lambda x: f"{pct(x['flags']['recall'])} ({x['flags']['tp']}/{x['flags']['tp'] + x['flags']['fn']})"),
        ("Flag precision", lambda x: f"{pct(x['flags']['precision'])} ({x['flags']['tp']}/{x['flags']['tp'] + x['flags']['fp']})"),
        ("Lines needing a person that were held", lambda x: pct(x["routing"]["lines_needing_a_person"]["recall"])),
        ("Lines sent without a person, and right", lambda x: acc(x["routing"]["auto_quoted_lines"])),
        ("**Silent errors** (wrong and not held)", lambda x: f"**{x['routing']['silent_errors']}**"),
        ("Time per RFQ, median / p90", lambda x: f"{secs(x['time']['median_active_s'])} / {secs(x['time']['p90_active_s'])}"),
        ("Time incl. free-tier rate-limit waits", lambda x: f"{secs(x['time']['median_s'])} / {secs(x['time']['p90_s'])}"),
        ("Model cost per RFQ (mean)", lambda x: f"${float(x['cost']['mean_usd']):.4f}" if x["cost"]["mean_usd"] else "–"),
        ("Failed RFQs", lambda x: str(x["failed"])),
    ]
    out = ["| | Dev (20 RFQs) | Holdout (10 RFQs) |", "|---|---:|---:|"]
    out += [f"| {label} | {f(s['dev'])} | {f(s['holdout'])} |" for label, f in rows]
    return out


def fields(r: dict) -> list[str]:
    s = r["splits"]
    out = ["| Field | Dev | Holdout |", "|---|---:|---:|"]
    out += [f"| {label} | {acc(s['dev']['extraction'].get(k))} | {acc(s['holdout']['extraction'].get(k))} |"
            for k, label in FIELDS.items()]
    return out


def methods(r: dict) -> list[str]:
    s = r["splits"]
    keys = [k for k in METHODS if k in s["dev"]["match"]["by_method"] or k in s["holdout"]["match"]["by_method"]]
    out = ["| Matched by | Dev | Holdout |", "|---|---:|---:|"]
    out += [f"| {METHODS[k]} | {acc(s['dev']['match']['by_method'].get(k))} | {acc(s['holdout']['match']['by_method'].get(k))} |"
            for k in keys]
    return out


def flags(r: dict) -> list[str]:
    codes = r["splits"]["all"]["flags"]["by_code"]
    out = ["| Flag (all 30 RFQs) | In gold | Caught | Missed | Extra |", "|---|---:|---:|---:|---:|"]
    out += [f"| `{c}` | {v['tp'] + v['fn']} | {v['tp']} | {v['fn']} | {v['fp']} |" for c, v in sorted(codes.items())]
    return out


def describe(r: dict) -> str:
    run = r["run"]
    who = "scripted perfect reader" if run["oracle"] else f"`{run['model']}`, reasoning effort {run['reasoning_effort']}"
    return (f"{who}; run `{r['file']}` at {run['at']}, code `{run['code']}`, rules `{run['rules_version']}`, "
            f"data `{run['data_version']}`.")


def compare(rows: list[tuple[str, dict]]) -> list[str]:
    out = ["| Model | Right SKU (holdout) | Exact price (holdout) | Silent errors (all) | Median time | Cost per RFQ |",
           "|---|---:|---:|---:|---:|---:|"]
    for name, r in rows:
        h, a = r["splits"]["holdout"], r["splits"]["all"]
        cost = f"${float(a['cost']['mean_usd']):.4f}" if a["cost"]["mean_usd"] else "–"
        out.append(f"| {name} | {pct(h['match']['overall']['accuracy'])} | {pct(h['price']['lines']['accuracy'])} | "
                   f"{a['routing']['silent_errors']} | {secs(a['time']['median_active_s'])} | {cost} |")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=settings().quotient_model)
    ap.add_argument("--compare", nargs="*", default=[])
    args = ap.parse_args()

    live, ceiling = latest(args.model), latest(None, oracle=True)
    if live is None:
        sys.exit(f"no complete live run of {args.model} in evals/results; run evals/run.py first")
    lines = [START, "", f"Live run: {describe(live)}", "", *headline(live), ""]
    others = [(m, r) for m in args.compare if (r := latest(m))]
    if others:
        lines += ["Other models on the same 30 RFQs:", "", *compare([(args.model, live), *others]), ""]
    lines += ["<details><summary>Reading accuracy per field, matching by method, flags by code</summary>", "",
              *fields(live), "", *methods(live), "", *flags(live), "", "</details>", ""]
    if ceiling:
        c = ceiling["splits"]["all"]
        lines += [f"Ceiling with a scripted perfect reader (the deterministic stages alone): right SKU "
                  f"{pct(c['match']['overall']['accuracy'])}, exact price {pct(c['price']['lines']['accuracy'])}, flag recall "
                  f"{pct(c['flags']['recall'])} and precision {pct(c['flags']['precision'])}, "
                  f"{c['routing']['silent_errors']} silent errors. So every miss above is a reading or matching error by the "
                  f"model, not a pricing error.", ""]
    lines.append(END)

    text = README.read_text()
    a, b = text.index(START), text.index(END) + len(END)
    README.write_text(text[:a] + "\n".join(lines) + text[b:])
    print(f"README tables from {live['file']}" + (f" and {ceiling['file']}" if ceiling else ""))


if __name__ == "__main__":
    main()
