"""Write the eval tables into README.md from the saved results, so no number in the README is typed by hand.

    uv run python evals/readme.py [--models qwen/qwen3.8-27b openai/gpt-oss-20b]

Uses each model's latest complete (30-RFQ) live run, or failing that its latest dev-only run, shown with the
holdout "not run"; by default every model that has either. Adds the latest scripted-reader run and replaces the
text between <!-- evals:start --> and <!-- evals:end -->.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.run import rescore  # noqa: E402
from quotient.llm import PRICES  # noqa: E402

RESULTS = ROOT / "evals" / "results"
README = ROOT / "README.md"
START, END = "<!-- evals:start -->", "<!-- evals:end -->"

FIELDS = {"line_found": "Line found", "quantity_written": "Quantity as written", "unit_written": "Unit as written",
          "grade_stated": "Grade stated", "cas": "CAS number", "quantity_in_uom": "Quantity in the SKU's unit",
          "pack": "Pack", "customer": "Customer", "incoterms_mentioned": "Incoterms mentioned",
          "incoterm": "Incoterm priced", "destination": "Destination", "currency": "Currency",
          "requested_date": "Requested date"}
METHODS = {"cas": "CAS number", "sku": "SKU code", "synonym": "Synonym table", "translation": "Reader's English name",
           "fuzzy": "Spelling match", "llm": "Model's pick among 5", "none": "No match (right when the gold has none)",
           "missed": "Line not found, or the RFQ failed"}


DEV = set(json.loads((ROOT / "evals" / "split.json").read_text())["dev"])


def latest(model: str | None, oracle: bool = False) -> dict | None:
    """The latest complete run; for a live model without one, its latest run of the whole dev split."""
    runs = [(p, json.loads(p.read_text())) for p in sorted(RESULTS.glob("*.json"), reverse=True)]
    runs = [(p, r) for p, r in runs if r["run"]["oracle"] == oracle and (oracle or r["run"]["model"] == model)]
    for p, r in runs:
        if r["run"]["rfqs"] == 30:
            return {**rescore(r), "file": p.name, "partial": False}
    for p, r in runs:
        if not oracle and {x["id"] for x in r["rfqs"]} == DEV:
            return {**rescore(r), "file": p.name, "partial": True}
    return None


def cell(r: dict, split: str, f) -> str:
    return f(r["splits"][split]) if split in r["splits"] else "not run"


def frac(c: int, t: int) -> str:
    return "–" if not t else f"{c}/{t} ({100 * c / t:.1f}%)"


def acc(a: dict | None) -> str:
    return "–" if not a else frac(a["correct"], a["total"])


def secs(x: float | None) -> str:
    return "–" if x is None else f"{x:.1f} s"


def short(model: str) -> str:
    return model.split("/", 1)[-1]


def setting(r: dict) -> str:
    effort = r["run"]["reasoning_effort"]
    return "instruct mode, no reasoning" if effort == "none" else f"reasoning effort {effort}"


def price(model: str) -> str:
    p_in, p_cached, p_out = PRICES[model]
    cached = f", cached input ${p_cached}" if p_cached != p_in else ""
    return f"{short(model)} ${p_in} input{cached}, ${p_out} output"


ROWS = [
    ("Right SKU (lines)", lambda x: acc(x["match"]["overall"])),
    ("Exact unit price and line total (priced gold lines)", lambda x: acc(x["price"]["lines"])),
    ("Exact quote total (RFQs)", lambda x: acc(x["price"]["quote_totals"])),
    ("Flag recall (gold flags caught)", lambda x: frac(x["flags"]["tp"], x["flags"]["tp"] + x["flags"]["fn"])),
    ("Flag precision (raised flags that were right)", lambda x: frac(x["flags"]["tp"], x["flags"]["tp"] + x["flags"]["fp"])),
    ("Lines needing a person that were held", lambda x: frac(x["routing"]["lines_needing_a_person"]["tp"],
                                                             x["routing"]["lines_needing_a_person"]["tp"]
                                                             + x["routing"]["lines_needing_a_person"]["fn"])),
    ("Lines sent without a person that were right", lambda x: acc(x["routing"]["auto_quoted_lines"])),
    ("**Silent errors** (wrong and not held)", lambda x: f"**{x['routing']['silent_errors']} of {x['lines']} lines**"),
    ("Time per RFQ, median / p90, excl. rate-limit waits", lambda x: f"{secs(x['time']['median_active_s'])} / {secs(x['time']['p90_active_s'])}"),
    ("Time per RFQ, median / p90, incl. waits (free tier)", lambda x: f"{secs(x['time']['median_s'])} / {secs(x['time']['p90_s'])}"),
    ("Model cost per RFQ, mean", lambda x: f"${float(x['cost']['mean_usd']):.4f}" if x["cost"]["mean_usd"] else "–"),
    ("Model calls per RFQ, mean", lambda x: f"{x['cost']['calls_per_rfq']:.1f}"),
    ("RFQs that failed", lambda x: f"{x['failed']} of {x['rfqs']}"),
]


def cached(r: dict) -> str:
    c = r["splits"]["all"]["cost"]
    model = r["run"]["model"]
    if PRICES[model][1] == PRICES[model][0]:
        return f"{short(model)}: Groq has no prompt caching for this model"
    if not c["cached_share"]:
        return f"{short(model)}: Groq reported no cached input tokens, so all input is charged at full price"
    return f"{short(model)}: {100 * c['cached_share']:.0f}% of input tokens were cached (half price)"


def origin(r: dict) -> str:
    parts = r["run"].get("combined_from")
    where = f"runs `{'` + `'.join(parts)}` (dev and holdout on different days, same prompts `{r['run']['prompts']}`)" \
        if parts else f"run `{r['file']}`" + (", dev RFQs only" if r["partial"] else "")
    return f"`{short(r['run']['model'])}` {setting(r)}, {where}, code `{r['run']['code']}`"


def live_table(runs: list[dict]) -> list[str]:
    head = ["| |"]
    for r in runs:
        s, m = r["splits"], short(r["run"]["model"])
        head += [f" {m}, dev ({s['dev']['rfqs']} RFQs, {s['dev']['lines']} lines) |",
                 f" {m}, holdout ({s['holdout']['rfqs']} RFQs, {s['holdout']['lines']} lines) |" if "holdout" in s
                 else f" {m}, holdout |"]
    out = ["".join(head), "|---|" + "---:|" * (2 * len(runs))]
    for label, f in ROWS:
        out.append(f"| {label} | " + " | ".join(cell(r, k, f) for r in runs for k in ("dev", "holdout")) + " |")
    return out


def detail(r: dict) -> list[str]:
    s = r["splits"]
    out = [f"<details><summary>{short(r['run']['model'])}: reading accuracy per field, matching by method, flags "
           f"by code</summary>", "", "| Field | Dev | Holdout |", "|---|---:|---:|"]
    out += [f"| {lab} | {cell(r, 'dev', lambda x, k=k: acc(x['extraction'].get(k)))} | "
            f"{cell(r, 'holdout', lambda x, k=k: acc(x['extraction'].get(k)))} |" for k, lab in FIELDS.items()]
    keys = [k for k in METHODS if any(k in x["match"]["by_method"] for x in s.values())]
    out += ["", "| Matched by | Dev | Holdout |", "|---|---:|---:|"]
    out += [f"| {METHODS[k]} | {cell(r, 'dev', lambda x, k=k: acc(x['match']['by_method'].get(k)))} | "
            f"{cell(r, 'holdout', lambda x, k=k: acc(x['match']['by_method'].get(k)))} |" for k in keys]
    rfqs = "all 30 RFQs" if "holdout" in s else "dev RFQs"
    out += ["", f"| Flag ({rfqs}) | In gold | Caught | Missed | Extra |", "|---|---:|---:|---:|---:|"]
    out += [f"| `{c}` | {v['tp'] + v['fn']} | {v['tp']} | {v['fn']} | {v['fp']} |"
            for c, v in sorted(s["all"]["flags"]["by_code"].items())]
    silent = s["all"]["routing"]["silent_error_lines"]
    out += ["", "Silent errors: " + ("; ".join(silent) if silent else "none") + ".", "", "</details>"]
    return out


def pipeline_test(r: dict) -> list[str]:
    c = r["splits"]["all"]
    return [
        "**Pipeline test: rules and pricing are correct given correct readings.** A scripted reader that returns the "
        "gold reading replaces the model, so only the deterministic stages are measured: matching rules, grades, "
        "units, header rules, pricing and flags. On all 30 RFQs: right SKU "
        f"{acc(c['match']['overall'])}, exact price {acc(c['price']['lines'])}, flag recall "
        f"{frac(c['flags']['tp'], c['flags']['tp'] + c['flags']['fn'])}, flag precision "
        f"{frac(c['flags']['tp'], c['flags']['tp'] + c['flags']['fp'])}, {c['routing']['silent_errors']} silent errors "
        f"(run `{r['file']}`). This is not a model result; it shows that every miss in the live tables above starts "
        "with the model's reading or with matching what it read.",
    ]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", help="default: every model with a complete live run, newest first")
    args = ap.parse_args()
    models = args.models or list(dict.fromkeys(
        r["run"]["model"] for p in sorted(RESULTS.glob("*.json"), reverse=True)
        if not (r := json.loads(p.read_text()))["run"]["oracle"]))
    runs = sorted((r for m in models if (r := latest(m))), key=lambda r: r["partial"])  # complete runs first
    if not runs:
        sys.exit("no live run in evals/results; run evals/run.py first")
    ceiling = latest(None, oracle=True)
    prices = sorted({r["run"].get("prices_checked") for r in runs} - {None})
    lines = [START, "", "Live models, one run each after the prompts were frozen:", "", *live_table(runs), ""]
    lines += ["; ".join(origin(r) for r in runs) + "."]
    lines += [f"Cost uses Groq's list prices per million tokens, checked on {', '.join(prices) or 'the run date'}: "
              + "; ".join(price(r["run"]["model"]) for r in runs) + ". " + "; ".join(cached(r) for r in runs) + ".", ""]
    for r in runs:
        lines += [*detail(r), ""]
    if ceiling:
        lines += [*pipeline_test(ceiling), ""]
    lines.append(END)
    text = README.read_text()
    a, b = text.index(START), text.index(END) + len(END)
    README.write_text(text[:a] + "\n".join(lines) + text[b:])
    print("README tables from " + ", ".join(r["file"] for r in runs) + (f" and {ceiling['file']}" if ceiling else ""))


if __name__ == "__main__":
    main()
