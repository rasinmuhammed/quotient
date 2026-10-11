"use client";

import { Download } from "lucide-react";
import { useMemo } from "react";
import { sampleFileUrl } from "@/lib/api";
import { date, FORMAT_LABEL } from "@/lib/format";
import type { QuoteDraft, Spanned } from "@/lib/types";
import { FormatIcon } from "../ui";
import { useReview } from "./context";

interface Mark {
  start: number;
  end: number;
  line: number | null; // null: a header value
  what: string;
}

const MAX_SPAN = 240; // longer "spans" are not a reading of one value; they are not highlighted

function locate(text: string, span: string, from = 0): [number, number] | null {
  const parts = span.trim().split(/\s+/).map((p) => p.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  if (!parts.length || !parts[0]) return null;
  const re = new RegExp(parts.join("\\s+"), "giu");
  re.lastIndex = from;
  const m = re.exec(text);
  return m ? [m.index, m.index + m[0].length] : null;
}

function marksFor(draft: QuoteDraft): Mark[] {
  const text = draft.document.text;
  const x = draft.extraction;
  if (!x) return [];
  const found: Mark[] = [];
  const add = (s: Spanned | { span: string | null; verified: boolean } | null | undefined, line: number | null, what: string) => {
    if (!s || !s.verified || !s.span || s.span.length > MAX_SPAN) return;
    const at = locate(text, s.span);
    if (at) found.push({ start: at[0], end: at[1], line, what });
  };
  draft.lines.forEach((ln) => {
    const e = ln.extracted;
    if (!e) return;
    add(e.mention, ln.line_no, "product");
    add(e.cas, ln.line_no, "CAS");
    add(e.grade, ln.line_no, "grade");
    add(e.quantity, ln.line_no, "quantity");
    if (e.unit.span !== e.quantity.span) add(e.unit, ln.line_no, "unit");
    add(e.packaging, ln.line_no, "packaging");
  });
  x.incoterms.forEach((i) => add(i, null, `incoterm ${i.term}`));
  add(x.destination_country, null, "destination");
  add(x.requested_date, null, "requested date");
  add(x.currency, null, "currency");
  add(x.document_grade, null, "grade for all lines");
  // Keep the first of overlapping marks, preferring the longer one where they start together.
  found.sort((a, b) => a.start - b.start || b.end - a.end);
  const out: Mark[] = [];
  for (const m of found) {
    const last = out[out.length - 1];
    if (last && m.start < last.end) continue;
    out.push(m);
  }
  return out;
}

export function DocumentPane() {
  const { draft, activeLine, setActiveLine, setOpenLine } = useReview();
  const doc = draft.document;
  const marks = useMemo(() => marksFor(draft), [draft]);
  const sample = doc.source_name.match(/^(rfq-\d{3})\./)?.[1];

  const pieces: React.ReactNode[] = [];
  let at = 0;
  marks.forEach((m, i) => {
    if (m.start > at) pieces.push(doc.text.slice(at, m.start));
    const active = m.line !== null && m.line === activeLine;
    pieces.push(
      <mark
        key={i}
        className="span"
        data-active={active}
        title={m.line ? `Line ${m.line}: ${m.what}` : m.what}
        onMouseEnter={() => m.line && setActiveLine(m.line)}
        onMouseLeave={() => setActiveLine(null)}
        onClick={() => {
          if (!m.line) return;
          setOpenLine(m.line);
          document.getElementById(`line-${m.line}`)?.scrollIntoView({ behavior: "smooth", block: "nearest" });
        }}
      >
        {doc.text.slice(m.start, m.end)}
      </mark>,
    );
    at = m.end;
  });
  if (at < doc.text.length) pieces.push(doc.text.slice(at));

  return (
    <section className="flex min-h-0 flex-col overflow-hidden rounded-2xl border border-border bg-surface shadow-card">
      <div className="flex items-center gap-3 border-b border-border px-4 py-3">
        <FormatIcon format={doc.format === "text" ? "txt" : doc.format} />
        <div className="min-w-0 flex-1">
          <div className="truncate text-[13px] font-semibold">{doc.subject || doc.source_name}</div>
          <div className="truncate text-[12px] text-muted">
            {FORMAT_LABEL[doc.format]} · {doc.sender_email ?? "sender unknown"}
            {doc.received ? ` · ${date(doc.received)}` : ""}
          </div>
        </div>
        {sample && (
          <a href={sampleFileUrl(sample)} title="Download the original file" className="rounded-md p-1.5 text-muted hover:bg-surface-2 hover:text-text">
            <Download className="size-4" />
          </a>
        )}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
        <pre className="font-mono text-[12.5px] leading-[1.7] whitespace-pre-wrap text-text/90 [overflow-wrap:anywhere]">{pieces}</pre>
      </div>
      <div className="border-t border-border px-4 py-2 text-[11.5px] text-faint">
        {marks.length
          ? "Underlined: text the reader took a value from. Hover a line to see its sources."
          : "As read by the pipeline. Values are verified against this text."}
      </div>
    </section>
  );
}
