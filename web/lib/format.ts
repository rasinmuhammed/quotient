import type { Dec, FlagCode, MatchMethod } from "./types";

/** Group digits of a decimal string without going through a float: "17988.86" -> "17,988.86". */
export function grouped(value: Dec | number | null | undefined, places?: number): string {
  if (value === null || value === undefined || value === "") return "–";
  let s = String(value);
  const neg = s.startsWith("-");
  if (neg) s = s.slice(1);
  let [int, frac = ""] = s.split(".");
  if (places !== undefined) {
    frac = frac.padEnd(places, "0").slice(0, places); // display only; the server already rounded
  } else {
    frac = frac.replace(/0+$/, "");
  }
  int = int.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return (neg ? "−" : "") + int + (frac ? `.${frac}` : "");
}

export function money(value: Dec | null | undefined, currency: string): string {
  if (value === null || value === undefined) return "–";
  return `${grouped(value, currency === "JPY" ? 0 : 2)}`;
}

export function unitMoney(value: Dec | null | undefined, currency: string): string {
  if (value === null || value === undefined) return "–";
  return grouped(value, currency === "JPY" ? 0 : 3);
}

export function qty(value: Dec | null | undefined): string {
  return grouped(value);
}

export function date(iso: string | null | undefined, opts: Intl.DateTimeFormatOptions = { day: "numeric", month: "short", year: "numeric" }) {
  if (!iso) return "–";
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString("en-GB", { ...opts, timeZone: "UTC" });
}

export function seconds(s: number | null | undefined): string {
  if (s === null || s === undefined) return "–";
  return s < 10 ? `${s.toFixed(1)} s` : `${Math.round(s)} s`;
}

export function pct(x: number | null | undefined, digits = 1): string {
  return x === null || x === undefined ? "–" : `${(x * 100).toFixed(digits)}%`;
}

export function usd(value: Dec | number | null | undefined): string {
  if (value === null || value === undefined) return "–";
  const n = Number(value);
  if (n === 0) return "$0";
  return n < 0.01 ? `$${n.toFixed(4)}` : `$${n.toFixed(3)}`;
}

export const GRADE_LABEL: Record<string, string> = {
  technical: "Technical",
  reagent: "Reagent",
  pharma_usp: "USP",
  pharma_ep: "Ph. Eur.",
};

export const METHOD_LABEL: Record<MatchMethod, string> = {
  cas: "CAS number",
  sku: "SKU code",
  synonym: "Synonym table",
  translation: "Translated name",
  fuzzy: "Spelling match",
  llm: "Model choice",
  manual: "Chosen by you",
  none: "No match",
};

export const FLAG_LABEL: Record<FlagCode, string> = {
  low_confidence_extraction: "Check reading",
  quantity_missing: "No quantity",
  no_match: "Not in range",
  ambiguous_match: "Unclear product",
  grade_not_specified: "Grade assumed",
  unit_conversion_impossible: "Unit unclear",
  ambiguous_unit: "Tons ambiguous",
  pack_unavailable: "Pack changed",
  below_moq: "Below MOQ",
  price_expired: "Price expired",
  no_price: "No price",
  margin_floor_applied: "Margin floor",
  incoterm_conflict: "Incoterm conflict",
  incoterm_unsupported: "Incoterm not priced",
  no_freight_lane: "No freight lane",
  hazmat_documents: "Hazmat docs",
  customer_unknown: "New customer",
};

export const SOURCE_LABEL: Record<string, string> = {
  document: "from the RFQ",
  customer: "customer default",
  customer_default: "customer default",
  conflict_customer_default: "conflict: customer's usual",
  conflict_first: "conflict: first named",
  fallback: "fallback",
  manual: "set by you",
  none: "unknown",
  email_domain: "email domain",
  company_name: "company name",
};

export const FORMAT_LABEL: Record<string, string> = { eml: "Email", txt: "Text", text: "Text", pdf: "PDF", xlsx: "Excel" };
