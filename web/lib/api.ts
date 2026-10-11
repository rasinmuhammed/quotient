import type { Edits, Health, QuoteDraft } from "./types";

export const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

/** Requests slower than this announce themselves: a free-tier server that slept takes ~30 s to wake. */
const SLOW_MS = 2500;
let pending = 0;
let slowTimer: ReturnType<typeof setTimeout> | null = null;

function announce(slow: boolean) {
  window.dispatchEvent(new CustomEvent("quotient:slow", { detail: slow }));
}

async function call<T>(path: string, init?: RequestInit & { as?: "json" | "blob" }): Promise<{ body: T; res: Response }> {
  let res: Response;
  if (pending++ === 0) slowTimer = setTimeout(() => announce(true), SLOW_MS);
  try {
    res = await fetch(`${API_URL}${path}`, init);
  } catch {
    throw new ApiError(0, "The quoting server cannot be reached. It may still be starting; try again in a moment.");
  } finally {
    if (--pending === 0) {
      if (slowTimer) clearTimeout(slowTimer);
      announce(false);
    }
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch {}
    throw new ApiError(res.status, detail);
  }
  const body = (init?.as === "blob" ? await res.blob() : await res.json()) as T;
  return { body, res };
}

const json = (payload: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(payload),
});

export const api = {
  health: () => call<Health>("/api/health").then((r) => r.body),

  /** Read an RFQ live: an upload, pasted text, or a sample read again by the model. */
  async quote(input: { file?: File; text?: string; sample?: string; live?: boolean }) {
    const form = new FormData();
    if (input.file) form.set("file", input.file);
    if (input.text) form.set("text", input.text);
    if (input.sample) form.set("sample", input.sample);
    if (input.live) form.set("live", "true");
    const { body, res } = await call<QuoteDraft>("/api/quotes", { method: "POST", body: form });
    return { draft: body, replay: res.headers.get("X-Quotient-Replay") };
  },
  reprice: (draft: QuoteDraft, edits: Edits = {}) =>
    call<QuoteDraft>("/api/quotes/reprice", json({ draft, edits })).then((r) => r.body),
  email: (draft: QuoteDraft) => call<QuoteDraft>("/api/quotes/email", json({ draft })).then((r) => r.body),
  pdf: (draft: QuoteDraft) => call<Blob>("/api/quotes/pdf", { ...json({ draft }), as: "blob" }).then((r) => r.body),
};
