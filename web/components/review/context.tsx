"use client";

import { createContext, useContext } from "react";
import type { CatalogData, Edits, QuoteDraft, RulesData } from "@/lib/types";

export interface ReviewState {
  draft: QuoteDraft;
  catalog: CatalogData | null;
  rules: RulesData | null;
  live: boolean;
  /** Apply edits on the server and replace the draft with the repriced one. */
  apply: (edits: Edits, busyKey?: string) => Promise<void>;
  busy: string | null;
  activeLine: number | null;
  setActiveLine: (n: number | null) => void;
  openLine: number | null;
  setOpenLine: (n: number | null) => void;
}

export const ReviewContext = createContext<ReviewState | null>(null);

export function useReview(): ReviewState {
  const ctx = useContext(ReviewContext);
  if (!ctx) throw new Error("useReview outside ReviewContext");
  return ctx;
}

export function ruleTitle(rules: RulesData | null, id: string | null | undefined): string | undefined {
  if (!rules || !id) return undefined;
  const r = rules.rules[id];
  return r ? `${id}: ${r.title}. ${r.text}` : id;
}
