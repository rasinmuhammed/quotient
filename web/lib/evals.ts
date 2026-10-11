import data from "@/data/evals.json";
import { modelLabel } from "./format";

// Shapes written by evals/run.py.

export interface Acc {
  correct: number;
  total: number;
  accuracy: number | null;
}
export interface PR {
  tp: number;
  fp: number;
  fn: number;
  recall: number | null;
  precision: number | null;
}

export interface SplitSummary {
  rfqs: number;
  failed: number;
  lines: number;
  extraction: Record<string, Acc>;
  match: { overall: Acc; by_method: Record<string, Acc>; extra_lines: number };
  price: { lines: Acc; quote_totals: Acc };
  flags: PR & { by_code: Record<string, PR> };
  routing: { lines_needing_a_person: PR; auto_quoted_lines: Acc; silent_errors: number; silent_error_lines: string[] };
  time: { median_s: number | null; p90_s: number | null; median_active_s: number | null; p90_active_s: number | null; stage_median_s: Record<string, number> };
  cost: { mean_usd: string | null; median_usd: string | null; p90_usd: string | null; total_usd: string; calls_per_rfq: number | null; prompt_tokens: number; cached_share: number | null; completion_tokens: number; reasoning_tokens: number };
  email: Record<string, number>;
}

export interface LineRecord {
  line_no: number;
  mention: string;
  pred_line: number | null;
  pred_mention: string | null;
  fields: Record<string, boolean | null>;
  sku: { gold: string | null; pred: string | null; method: string; ok: boolean };
  price: { gold_unit: string | null; pred_unit: string | null; gold_total: string | null; pred_total: string | null; exact: boolean | null };
  flags: { gold: string[]; pred: string[] };
  needs_human: { gold: boolean; pred: boolean | null };
  silent_error: boolean;
}

export interface RfqRecord {
  id: string;
  format: string;
  error: string | null;
  lines: LineRecord[];
  extra_lines: { line_no: number; mention: string | null; sku: string | null; flags: string[]; needs_human: boolean; silent_error: boolean }[];
  header: Record<string, boolean | null>;
  quote_flags: { gold: string[]; pred: string[] };
  total: { gold: string | null; pred: string | null; exact: boolean };
  needs_human: { gold: boolean; pred: boolean | null };
  email: { source: string; check: string } | null;
  timing: { wall_s: number; active_s: number; waited_s: number; stages: Record<string, number> } | null;
  llm: { calls: number; cost_usd: string; prompt_tokens: number; cached_tokens: number; completion_tokens: number } | null;
}

export interface EvalRun {
  name: string;
  run: {
    at: string;
    model: string;
    reasoning_effort: string | null;
    oracle: boolean;
    code: string;
    rules_version: string;
    data_version: string;
    rfqs: number;
    wall_s: number;
    workers: number;
    holdout_ids: string[];
  };
  splits: Partial<Record<"dev" | "holdout" | "all", SplitSummary>>;
  rfqs: RfqRecord[];
}

export const RUNS = (data as unknown as { runs: EvalRun[] }).runs;

export function runLabel(r: EvalRun): string {
  return r.run.oracle ? "Pipeline test (scripted reader)" : modelLabel(r.run.model, r.run.reasoning_effort);
}

/** The run the Evals page opens on: the newest live run, else the pipeline test. */
export function defaultRun(): EvalRun | undefined {
  return RUNS.find((r) => !r.run.oracle) ?? RUNS[0];
}

export const FIELD_LABEL: Record<string, string> = {
  line_found: "Line found",
  quantity_written: "Quantity as written",
  unit_written: "Unit as written",
  grade_stated: "Grade stated",
  cas: "CAS number",
  quantity_in_uom: "Quantity in SKU unit",
  pack: "Pack",
  customer: "Customer",
  incoterms_mentioned: "Incoterms mentioned",
  incoterm: "Incoterm priced",
  destination: "Destination",
  currency: "Currency",
  requested_date: "Requested date",
};
