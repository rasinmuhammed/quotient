import type { QuoteDraft } from "./types";

// The server keeps no drafts: the browser holds the one being reviewed, for this tab's session only.

const key = (id: string) => `quotient:draft:${id}`;

export interface Stored {
  draft: QuoteDraft;
  replay: string | null;
  sample: string | null;
}

export function saveDraft(s: Stored) {
  try {
    sessionStorage.setItem(key(s.draft.id), JSON.stringify(s));
  } catch {}
}

export function loadDraft(id: string): Stored | null {
  try {
    const raw = sessionStorage.getItem(key(id));
    return raw ? (JSON.parse(raw) as Stored) : null;
  } catch {
    return null;
  }
}
