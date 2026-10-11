"""Publish the demo's static data into the web app, so opening a sample never waits on the API.

    uv run python evals/publish.py

Writes, all generated (do not edit by hand):
  web/public/demo/drafts/<rfq>.json   the drafts the default model produced in its latest complete eval run
  web/public/demo/rfqs/<file>         the original RFQ files, for download
  web/data/samples.json               the sample list
  web/data/catalog.json, rules.json   what the review screen shows without the API (product picker, rule texts)
  web/data/evals.json                 the eval runs for the Evals page
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from quotient.api import _sample_index, catalog, rules  # noqa: E402
from quotient.config import settings  # noqa: E402

EVALS, RESULTS = ROOT / "evals", ROOT / "evals" / "results"
WEB = ROOT / "web"
KEEP_RUNS = 6


def write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n")


def complete_runs() -> list[tuple[Path, dict]]:
    out = []
    for p in sorted(RESULTS.glob("*.json"), reverse=True):
        r = json.loads(p.read_text())
        if r["run"]["rfqs"] == 30:
            out.append((p, r))
    return out


def main() -> None:
    model = settings().quotient_model
    runs = complete_runs()
    live = [(p, r) for p, r in runs if not r["run"]["oracle"]]
    oracle = next(((p, r) for p, r in runs if r["run"]["oracle"]), None)

    # Drafts: only from the default model's latest complete run, all 30 from that one run.
    source = next(((p, r) for p, r in live if r["run"]["model"] == model), None)
    drafts_dir = WEB / "public" / "demo" / "drafts"
    shutil.rmtree(drafts_dir, ignore_errors=True)
    replay = {}
    if source:
        saved = RESULTS / "drafts" / model.replace("/", "_")
        for f in sorted(saved.glob("rfq-*.json")):
            d = json.loads(f.read_text())
            if d["run"] != source[0].name:
                sys.exit(f"{f.name} is from {d['run']}, not the latest complete run {source[0].name}; rerun evals/run.py")
            write(drafts_dir / f.name, d["draft"])
            replay[f.stem] = {"run": d["run"], "model": d["model"], "effort": d.get("effort")}

    rfq_dir = WEB / "public" / "demo" / "rfqs"
    shutil.rmtree(rfq_dir, ignore_errors=True)
    rfq_dir.mkdir(parents=True)
    for f in sorted((EVALS / "rfqs").iterdir()):
        shutil.copy2(f, rfq_dir / f.name)

    samples = [{**s, "replay": replay.get(s["id"])} for s in _sample_index()]
    write(WEB / "data" / "samples.json", samples)
    write(WEB / "data" / "catalog.json", catalog())
    write(WEB / "data" / "rules.json", rules())
    chosen = [r for _, r in live[: KEEP_RUNS - 1]] + ([oracle[1]] if oracle else [])
    names = [p.stem for p, _ in live[: KEEP_RUNS - 1]] + ([oracle[0].stem] if oracle else [])
    write(WEB / "data" / "evals.json", {"runs": [{"name": n, **r} for n, r in zip(names, chosen, strict=True)]})
    print(f"published {len(replay)} drafts from {source[0].name if source else 'no live run'}, "
          f"{len(samples)} samples, {len(chosen)} eval runs")


if __name__ == "__main__":
    main()
