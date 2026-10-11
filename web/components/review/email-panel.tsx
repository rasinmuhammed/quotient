"use client";

import { Check, Copy, Download, FileJson, RefreshCw, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { QuoteDraft } from "@/lib/types";
import { Button } from "../ui";
import { useReview } from "./context";

/** What the email states: if this changes after the email was written, the email is out of date. */
function fingerprint(d: QuoteDraft): string {
  const h = d.header;
  return JSON.stringify([h.incoterm, h.currency, h.customer.customer_id, d.header_approved,
    d.lines.map((l) => [l.line_no, l.match.sku, l.priced?.unit_price, l.priced?.line_total, l.needs_human])]);
}

function save(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob);
  const a = Object.assign(document.createElement("a"), { href: url, download: name });
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function EmailPanel({ onDraft }: { onDraft: (d: QuoteDraft) => void }) {
  const { draft, live } = useReview();
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState<"email" | "pdf" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const email = draft.email;
  const [writtenFor, setWrittenFor] = useState(() => fingerprint(draft));

  async function run<T>(kind: "email" | "pdf", fn: () => Promise<T>) {
    setBusy(kind);
    setError(null);
    try {
      return await fn();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  const stale = Boolean(email) && fingerprint(draft) !== writtenFor;

  return (
    <section className="overflow-hidden rounded-2xl border border-border bg-surface shadow-card">
      <div className="flex flex-wrap items-center gap-2 border-b border-border px-4 py-3">
        <div className="mr-auto">
          <h2 className="text-[14px] font-semibold">Reply to the customer</h2>
          <p className="text-[12px] text-muted">
            {draft.needs_human ? "Draft: held lines go out as “to be confirmed” unless you resolve them." : "Every line is priced or resolved."}
          </p>
        </div>
        {email && (
          <Button
            onClick={() => {
              navigator.clipboard.writeText(`Subject: ${email.subject}\n\n${email.body}`);
              setCopied(true);
              setTimeout(() => setCopied(false), 1600);
            }}
          >
            {copied ? <Check className="size-4 text-accent" /> : <Copy className="size-4" />} {copied ? "Copied" : "Copy"}
          </Button>
        )}
        <Button
          onClick={() =>
            run("email", () =>
              api.email(draft).then((d) => {
                setWrittenFor(fingerprint(d));
                onDraft(d);
              }),
            )
          }
          disabled={busy !== null}
          title={live ? "Write the email again from the current quote" : "Rebuild the email from the current quote (template: no model on the server)"}
        >
          <RefreshCw className={`size-4 ${busy === "email" ? "animate-spin" : ""}`} /> {stale ? "Update email" : "Redraft"}
        </Button>
        <Button kind="primary" onClick={() => run("pdf", () => api.pdf(draft).then((b) => save(b, `${draft.id}.pdf`)))} disabled={busy !== null}>
          <Download className="size-4" /> Quote PDF
        </Button>
        <Button kind="ghost" title="The full draft with every source row and rule, as JSON" onClick={() => save(new Blob([JSON.stringify(draft, null, 1)], { type: "application/json" }), `${draft.id}.json`)}>
          <FileJson className="size-4" />
        </Button>
      </div>
      {error && <div className="border-b border-border bg-bad-soft px-4 py-2 text-[13px] text-bad">{error}</div>}
      {email ? (
        <div className="px-5 py-4">
          <div className="text-[13px]">
            <span className="text-muted">Subject </span>
            <span className="font-medium">{email.subject}</span>
          </div>
          <pre className="mt-3 font-sans text-[13.5px] leading-relaxed whitespace-pre-wrap text-text/90">{email.body}</pre>
          <div className="mt-4 flex items-start gap-2 rounded-lg bg-surface-2 px-3 py-2 text-[12px] text-muted">
            <ShieldCheck className="mt-px size-4 shrink-0 text-accent" />
            <span>
              {email.source === "llm" ? `Written by ${draft.model.replace(/^openai\//, "")}. ` : "Built from the template. "}
              Number check: {email.check}.{stale && " The quote changed since: update the email before sending."}
            </span>
          </div>
        </div>
      ) : (
        <div className="px-5 py-8 text-center text-[13px] text-muted">No email yet. Redraft to write one from the quote.</div>
      )}
    </section>
  );
}
