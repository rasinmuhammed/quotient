"use client";

import { Check, ChevronRight, X } from "lucide-react";
import Link from "next/link";
import { Fragment, useState } from "react";
import type { RfqRecord } from "@/lib/evals";
import { FLAG_LABEL, FORMAT_LABEL, grouped, seconds, usd } from "@/lib/format";
import type { FlagCode } from "@/lib/types";

function Mark({ ok }: { ok: boolean | null }) {
  if (ok === null) return <span className="text-faint">–</span>;
  return ok ? <Check className="inline size-3.5 text-accent" /> : <X className="inline size-3.5 text-bad" />;
}

function flagDiff(gold: string[], pred: string[]) {
  const g = new Set(gold);
  const p = new Set(pred);
  return {
    missed: gold.filter((c) => !p.has(c)),
    extra: pred.filter((c) => !g.has(c)),
  };
}

export function RfqTable({ rfqs, holdout }: { rfqs: RfqRecord[]; holdout: string[] }) {
  const [open, setOpen] = useState<string | null>(null);
  const hold = new Set(holdout);
  return (
    <table className="w-full text-[13px]">
      <thead className="border-b border-border text-[12px] text-muted">
        <tr>
          <th className="w-8" />
          <th className="px-2 py-2 text-left font-medium">RFQ</th>
          <th className="px-3 py-2 text-left font-medium">Format</th>
          <th className="px-3 py-2 text-right font-medium">Right SKU</th>
          <th className="px-3 py-2 text-right font-medium">Exact price</th>
          <th className="px-3 py-2 text-right font-medium">Flags missed / extra</th>
          <th className="px-3 py-2 text-right font-medium">Silent errors</th>
          <th className="px-3 py-2 text-right font-medium">Time</th>
          <th className="px-4 py-2 text-right font-medium">Cost</th>
        </tr>
      </thead>
      <tbody>
        {rfqs.map((r) => {
          const priced = r.lines.filter((l) => l.price.exact !== null);
          let missed = 0;
          let extra = 0;
          for (const l of r.lines) {
            const d = flagDiff(l.flags.gold, l.flags.pred);
            missed += d.missed.length;
            extra += d.extra.length;
          }
          const q = flagDiff(r.quote_flags.gold, r.quote_flags.pred);
          missed += q.missed.length;
          extra += q.extra.length + r.extra_lines.reduce((a, x) => a + x.flags.length, 0);
          const silent = r.lines.filter((l) => l.silent_error).length + r.extra_lines.filter((x) => x.silent_error).length;
          const isOpen = open === r.id;
          return (
            <Fragment key={r.id}>
              <tr onClick={() => setOpen(isOpen ? null : r.id)} className="cursor-pointer border-b border-border hover:bg-surface-2/60">
                <td className="pl-3">
                  <ChevronRight className={`size-4 text-faint transition-transform ${isOpen ? "rotate-90" : ""}`} />
                </td>
                <td className="px-2 py-2">
                  <span className="num font-mono text-[12px]">{r.id}</span>
                  {hold.has(r.id) && <span className="ml-2 rounded bg-surface-3 px-1.5 text-[11px] text-muted">holdout</span>}
                  {r.error && <span className="ml-2 rounded bg-bad-soft px-1.5 text-[11px] text-bad" title={r.error}>failed</span>}
                </td>
                <td className="px-3 py-2 text-muted">{FORMAT_LABEL[r.format]}</td>
                <td className="num px-3 py-2 text-right">
                  {r.lines.filter((l) => l.sku.ok).length}/{r.lines.length}
                </td>
                <td className="num px-3 py-2 text-right">
                  {priced.filter((l) => l.price.exact).length}/{priced.length}
                </td>
                <td className="num px-3 py-2 text-right">
                  <span className={missed ? "text-bad" : "text-faint"}>{missed}</span>
                  <span className="text-faint"> / </span>
                  <span className={extra ? "text-attn" : "text-faint"}>{extra}</span>
                </td>
                <td className={`num px-3 py-2 text-right ${silent ? "font-medium text-bad" : "text-faint"}`}>{silent}</td>
                <td className="num px-3 py-2 text-right text-muted">{r.timing ? seconds(r.timing.active_s) : "–"}</td>
                <td className="num px-4 py-2 text-right text-muted">{r.llm ? usd(r.llm.cost_usd) : "–"}</td>
              </tr>
              {isOpen && (
                <tr className="border-b border-border bg-surface-2/40">
                  <td />
                  <td colSpan={8} className="px-2 pt-2 pb-4">
                    <Detail r={r} />
                  </td>
                </tr>
              )}
            </Fragment>
          );
        })}
      </tbody>
    </table>
  );
}

function FlagList({ codes, tone }: { codes: string[]; tone: "bad" | "attn" }) {
  if (!codes.length) return null;
  return (
    <span className={tone === "bad" ? "text-bad" : "text-attn"}>
      {tone === "bad" ? "missed " : "extra "}
      {codes.map((c) => FLAG_LABEL[c as FlagCode] ?? c).join(", ")}
    </span>
  );
}

function Detail({ r }: { r: RfqRecord }) {
  const headerWrong = Object.entries(r.header).filter(([, v]) => v === false).map(([k]) => k);
  const q = flagDiff(r.quote_flags.gold, r.quote_flags.pred);
  return (
    <div className="space-y-3">
      {r.error && <div className="rounded-lg bg-bad-soft px-3 py-2 text-[12.5px] text-bad">{r.error}</div>}
      <table className="w-full text-[12.5px]">
        <thead className="text-[11.5px] text-faint">
          <tr>
            <th className="py-1 pr-3 text-left font-medium">#</th>
            <th className="py-1 pr-3 text-left font-medium">Customer wrote</th>
            <th className="py-1 pr-3 text-left font-medium">SKU (gold → got)</th>
            <th className="py-1 pr-3 text-right font-medium">Unit price (gold → got)</th>
            <th className="py-1 pr-3 text-left font-medium">Fields</th>
            <th className="py-1 text-left font-medium">Flags</th>
          </tr>
        </thead>
        <tbody>
          {r.lines.map((l) => {
            const d = flagDiff(l.flags.gold, l.flags.pred);
            const wrongFields = Object.entries(l.fields).filter(([, v]) => v === false).map(([k]) => k);
            return (
              <tr key={l.line_no} className={`align-top ${l.silent_error ? "text-bad" : ""}`}>
                <td className="num py-1 pr-3">{l.line_no}</td>
                <td className="py-1 pr-3">
                  {l.mention}
                  {l.pred_line === null && <span className="ml-1.5 text-bad">(not found)</span>}
                </td>
                <td className="py-1 pr-3 font-mono text-[11.5px]">
                  <Mark ok={l.sku.ok} /> {l.sku.gold ?? "none"}
                  {!l.sku.ok && <> → {l.sku.pred ?? "none"}</>} <span className="font-sans text-faint">({l.sku.method})</span>
                </td>
                <td className="num py-1 pr-3 text-right">
                  <Mark ok={l.price.exact} /> {grouped(l.price.gold_unit)}
                  {l.price.exact === false && <> → {grouped(l.price.pred_unit)}</>}
                </td>
                <td className="py-1 pr-3 text-muted">{wrongFields.length ? `wrong: ${wrongFields.join(", ")}` : "all right"}</td>
                <td className="py-1">
                  {!d.missed.length && !d.extra.length ? (
                    <span className="text-faint">{l.flags.gold.length ? "all caught" : "none"}</span>
                  ) : (
                    <span className="space-x-2">
                      <FlagList codes={d.missed} tone="bad" />
                      <FlagList codes={d.extra} tone="attn" />
                    </span>
                  )}
                </td>
              </tr>
            );
          })}
          {r.extra_lines.map((x) => (
            <tr key={`x${x.line_no}`} className="text-attn">
              <td className="py-1 pr-3">+</td>
              <td className="py-1 pr-3" colSpan={5}>
                Extra line read: {x.mention ?? "?"} → {x.sku ?? "no match"} {x.silent_error ? "(would be quoted)" : "(held for a person)"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="flex flex-wrap gap-x-5 gap-y-1 text-[12px] text-muted">
        <span>Header: {headerWrong.length ? <span className="text-bad">wrong {headerWrong.join(", ")}</span> : "all right"}</span>
        {(q.missed.length > 0 || q.extra.length > 0) && (
          <span className="space-x-2">
            Quote flags: <FlagList codes={q.missed} tone="bad" /> <FlagList codes={q.extra} tone="attn" />
          </span>
        )}
        <span>
          Total: {r.total.exact ? "exact" : <span className="text-bad">{grouped(r.total.gold)} → {grouped(r.total.pred)}</span>}
        </span>
        {r.email && <span title={r.email.check}>Email: {r.email.source === "llm" ? "model-written, checked" : "template"}</span>}
        <Link href={`/review?sample=${r.id}`} className="font-medium text-accent hover:text-accent-strong">
          Open in review →
        </Link>
      </div>
    </div>
  );
}
