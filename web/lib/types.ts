// Mirrors quotient/pipeline/types.py and quotient/domain.py. Money and quantities arrive as strings
// (Python Decimal) and stay strings: the browser formats them, it never does arithmetic on prices.

export type Dec = string;
export type Severity = "block" | "warn";

export type FlagCode =
  | "low_confidence_extraction"
  | "quantity_missing"
  | "no_match"
  | "ambiguous_match"
  | "grade_not_specified"
  | "unit_conversion_impossible"
  | "ambiguous_unit"
  | "pack_unavailable"
  | "below_moq"
  | "price_expired"
  | "no_price"
  | "margin_floor_applied"
  | "incoterm_conflict"
  | "incoterm_unsupported"
  | "no_freight_lane"
  | "hazmat_documents"
  | "customer_unknown";

export interface Flag {
  code: FlagCode;
  severity: Severity;
  message: string;
  rule_id: string | null;
}

export interface Spanned {
  value: string | null;
  span: string | null;
  verified: boolean;
}

export interface ExtractedLine {
  mention: Spanned;
  english_name: string | null;
  cas: Spanned;
  grade: Spanned;
  quantity: Spanned;
  unit: Spanned;
  packaging: Spanned;
  confidence: number;
  note: string | null;
}

export interface IncotermMention {
  term: string;
  place: string | null;
  span: string | null;
  verified: boolean;
}

export interface Extraction {
  buyer_company: Spanned;
  incoterms: IncotermMention[];
  destination_country: Spanned;
  requested_date: Spanned;
  currency: Spanned;
  document_grade: Spanned;
  lines: ExtractedLine[];
}

export type MatchMethod = "cas" | "sku" | "synonym" | "translation" | "fuzzy" | "llm" | "manual" | "none";

export interface Candidate {
  chemical_code: string;
  name: string;
  score: number;
}

export interface Match {
  chemical_code: string | null;
  sku: string | null;
  method: MatchMethod;
  matched_on: string | null;
  score: number | null;
  grade: string | null;
  grade_source: "line" | "document" | "only_grade" | "default" | "manual" | "none";
  candidates: Candidate[];
  explanation: string;
}

export interface Quantity {
  value: Dec | null;
  uom: string | null;
  pack_code: string | null;
  steps: string[];
}

export interface Step {
  rule_id: string;
  label: string;
  value: Dec | null;
  unit: string;
  source: string | null;
}

export interface PricedLine {
  sku: string;
  product_name: string;
  uom: string;
  pack_code: string;
  pack_description: string;
  pack_size: Dec;
  packs: number;
  requested_qty: Dec;
  quoted_qty: Dec;
  incoterm: string;
  currency: string;
  list_unit_usd: Dec | null;
  discount_pct: Dec;
  net_unit_usd: Dec | null;
  freight_unit_usd: Dec | null;
  delivered_unit_usd: Dec | null;
  unit_price: Dec | null;
  line_total: Dec | null;
  price_row_id: string | null;
  price_tier: number | null;
  freight_row_id: string | null;
  fx_row_id: string;
  fx_rate: Dec;
  fx_snapshot_date: string;
  lead_time_days: number;
  transit_days: number | null;
  rules_applied: string[];
  steps: Step[];
  flags: Flag[];
}

export interface LineResult {
  line_no: number;
  extracted: ExtractedLine | null;
  match: Match;
  quantity: Quantity;
  priced: PricedLine | null;
  reading_flags: Flag[];
  edited: boolean;
  approved: boolean;
  flags: Flag[]; // computed by the server
  needs_human: boolean; // computed by the server
}

export interface CustomerMatch {
  customer_id: string | null;
  name: string | null;
  method: "email_domain" | "company_name" | "manual" | "none";
  matched_on: string | null;
  score: number | null;
}

export interface Header {
  customer: CustomerMatch;
  incoterms_mentioned: string[];
  incoterm: string;
  incoterm_source: string;
  destination_country: string | null;
  destination_source: string;
  currency: string;
  currency_source: string;
  requested_date: string | null;
  quote_date: string;
}

export interface RFQDocument {
  source_name: string;
  format: "text" | "eml" | "pdf" | "xlsx";
  text: string;
  sender_email: string | null;
  sender_name: string | null;
  subject: string | null;
  received: string | null;
}

export interface LLMUsage {
  calls: number;
  prompt_tokens: number;
  cached_tokens: number;
  completion_tokens: number;
  reasoning_tokens: number;
  cost_usd: Dec;
  seconds: number;
  waited_seconds: number;
  models: string[];
}

export interface QuoteDraft {
  id: string;
  document: RFQDocument;
  extraction: Extraction | null;
  header: Header;
  lines: LineResult[];
  quote_flags: Flag[];
  header_approved: boolean;
  currency: string;
  total: Dec | null;
  valid_until: string | null;
  email: { subject: string; body: string; source: "llm" | "template"; check: string } | null;
  timings: { stage: string; seconds: number }[];
  llm: LLMUsage;
  rules_version: string;
  data_version: string;
  model: string;
  needs_human: boolean;
}

export interface LineEdit {
  line_no?: number | null;
  sku?: string;
  quantity?: Dec;
  pack_code?: string;
  approved?: boolean;
  remove?: boolean;
}

export interface Edits {
  customer_id?: string;
  incoterm?: string;
  destination_country?: string;
  currency?: string;
  header_approved?: boolean;
  lines?: LineEdit[];
}

export interface CatalogProduct {
  sku: string;
  name: string;
  chemical: string;
  cas: string;
  grade: string;
  grade_label: string;
  uom: string;
  form: string;
  packs: string[];
  moq: Dec;
  hazard: string | null;
}

export interface CatalogData {
  products: CatalogProduct[];
  packs: Record<string, { description: string; size: Dec; uom: string; form: string }>;
  customers: { id: string; name: string; country: string; currency: string; incoterm: string; tier: string }[];
  incoterms: string[];
  currencies: string[];
  countries: string[];
}

export interface RulesData {
  name: string;
  version: string;
  rules: Record<string, { title: string; text: string; params: Record<string, unknown> }>;
}

export interface Sample {
  id: string;
  file: string;
  format: "eml" | "txt" | "pdf" | "xlsx";
  split: "dev" | "holdout";
  title: string;
  lines: number;
  sender: string | null;
  received: string;
  replay: boolean;
}

export interface Health {
  ok: boolean;
  model: string | null;
  reasoning_effort: string;
  live: boolean;
  tracing: boolean;
  rules_version: string;
  data_version: string;
  fx_snapshot: string;
}
