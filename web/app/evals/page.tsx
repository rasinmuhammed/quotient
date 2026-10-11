import type { Metadata } from "next";
import { EvalReport } from "@/components/evals/report";
import { defaultRun } from "@/lib/evals";

export const metadata: Metadata = { title: "Evals" };

export default function EvalsPage() {
  const run = defaultRun();
  if (!run) return <p className="mx-auto mt-24 text-muted">No eval runs yet. Run `uv run python evals/run.py`.</p>;
  return <EvalReport run={run} />;
}
