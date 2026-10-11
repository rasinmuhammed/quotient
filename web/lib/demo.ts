// Static demo data written by `uv run python evals/publish.py`. Opening a sample reads these and never
// waits on the API, which sleeps on Render's free tier.
import catalogData from "@/data/catalog.json";
import rulesData from "@/data/rules.json";
import samplesData from "@/data/samples.json";
import type { CatalogData, QuoteDraft, RulesData, Sample } from "./types";

export const SAMPLES = samplesData as unknown as Sample[];
export const CATALOG = catalogData as unknown as CatalogData;
export const RULES = rulesData as unknown as RulesData;

export function sampleFile(file: string) {
  return `/demo/rfqs/${file}`;
}

/** The draft the model produced for a sample in the published eval run, or null if none was published. */
export async function savedDraft(id: string): Promise<QuoteDraft | null> {
  const res = await fetch(`/demo/drafts/${encodeURIComponent(id)}.json`);
  return res.ok ? ((await res.json()) as QuoteDraft) : null;
}
