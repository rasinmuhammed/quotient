"use client";

import { Check, TriangleAlert, Undo2 } from "lucide-react";
import { date, money, SOURCE_LABEL } from "@/lib/format";
import { Button, FlagChip, RuleTag } from "../ui";
import { ruleTitle, useReview } from "./context";

function Field({ label, children, hint }: { label: string; children: React.ReactNode; hint?: string }) {
  return (
    <div className="min-w-0 px-4 py-3">
      <div className="text-[11.5px] text-muted">{label}</div>
      <div className="mt-0.5 truncate text-[14px] font-medium">{children}</div>
      {hint && <div className="mt-0.5 truncate text-[11.5px] text-faint">{hint}</div>}
    </div>
  );
}

function Select({ value, options, onChange, label }: { value: string; options: { value: string; label: string }[]; onChange: (v: string) => void; label: string }) {
  return (
    <select
      aria-label={label}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="-ml-1 max-w-full cursor-pointer appearance-none truncate rounded-md bg-transparent px-1 py-0.5 text-[14px] font-medium hover:bg-surface-2 focus:bg-surface-2 focus:outline-none"
    >
      {!options.some((o) => o.value === value) && <option value={value}>{value}</option>}
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  );
}

function totalHint(lines: { priced: { line_total: string | null } | null; needs_human: boolean }[]): string {
  const unpriced = lines.filter((l) => !l.priced?.line_total).length;
  const held = lines.filter((l) => l.priced?.line_total && l.needs_human).length;
  const parts = [];
  if (held) parts.push(`incl. ${held} line${held === 1 ? "" : "s"} to confirm`);
  if (unpriced) parts.push(`${unpriced} unpriced`);
  return parts.join(" · ") || `${lines.length} line${lines.length === 1 ? "" : "s"}, all confirmed`;
}

export function Summary() {
  const { draft, catalog, apply } = useReview();
  const h = draft.header;
  const held = draft.lines.filter((l) => l.needs_human).length;
  const headerHeld = !draft.header_approved && draft.quote_flags.some((f) => f.severity === "block");
  const ready = !draft.needs_human;

  return (
    <section className="overflow-hidden rounded-2xl border border-border bg-surface shadow-card">
      <div className="grid grid-cols-2 divide-border sm:grid-cols-3 lg:grid-cols-[1.6fr_1fr_0.8fr_0.8fr_1fr_1.3fr] lg:divide-x">
        <Field label="Customer" hint={h.customer.customer_id ? `${h.customer.customer_id} · matched by ${SOURCE_LABEL[h.customer.method]}` : "not in the customer list"}>
          {catalog ? (
            <Select
              label="Customer"
              value={h.customer.customer_id ?? ""}
              onChange={(v) => v && apply({ customer_id: v }, "header")}
              options={[
                ...(h.customer.customer_id ? [] : [{ value: "", label: h.customer.name ?? "Unknown sender" }]),
                ...catalog.customers.map((c) => ({ value: c.id, label: c.name })),
              ]}
            />
          ) : (
            (h.customer.name ?? "Unknown sender")
          )}
        </Field>
        <Field label="Incoterm" hint={`${SOURCE_LABEL[h.incoterm_source] ?? h.incoterm_source}${h.destination_country ? ` · to ${h.destination_country}` : ""}`}>
          {catalog ? (
            <Select label="Incoterm" value={h.incoterm} onChange={(v) => apply({ incoterm: v }, "header")} options={catalog.incoterms.map((t) => ({ value: t, label: t }))} />
          ) : (
            h.incoterm
          )}
        </Field>
        <Field label="Currency" hint={SOURCE_LABEL[h.currency_source] ?? h.currency_source}>
          {catalog ? (
            <Select label="Currency" value={h.currency} onChange={(v) => apply({ currency: v }, "header")} options={catalog.currencies.map((c) => ({ value: c, label: c }))} />
          ) : (
            h.currency
          )}
        </Field>
        <Field label="Valid until" hint={h.requested_date ? `wanted by ${date(h.requested_date)}` : "no date requested"}>
          <span className="num">{date(draft.valid_until)}</span>
        </Field>
        <Field label="Total" hint={totalHint(draft.lines)}>
          <span className="num text-[18px] font-semibold tracking-[-0.01em]">
            {draft.total ? money(draft.total, draft.currency) : "–"} <span className="text-[13px] font-medium text-muted">{draft.currency}</span>
          </span>
        </Field>
        <div className="col-span-2 flex items-center px-4 py-3 sm:col-span-1">
          {ready ? (
            <div className="flex items-center gap-2.5">
              <span className="grid size-8 place-items-center rounded-full bg-accent-soft text-accent">
                <Check className="size-4" />
              </span>
              <div>
                <div className="text-[13.5px] font-semibold text-accent">Ready to send</div>
                <div className="text-[11.5px] text-muted">Nothing left for you to check</div>
              </div>
            </div>
          ) : (
            <div className="flex items-center gap-2.5">
              <span className="grid size-8 place-items-center rounded-full bg-attn-soft text-attn">
                <TriangleAlert className="size-4" />
              </span>
              <div>
                <div className="text-[13.5px] font-semibold text-attn">
                  {held + (headerHeld ? 1 : 0)} item{held + (headerHeld ? 1 : 0) === 1 ? "" : "s"} need{held + (headerHeld ? 1 : 0) === 1 ? "s" : ""} you
                </div>
                <div className="text-[11.5px] text-muted">
                  {draft.lines.length - held} of {draft.lines.length} lines ready
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
      <QuoteFlags />
    </section>
  );
}

function QuoteFlags() {
  const { draft, rules, apply } = useReview();
  if (!draft.quote_flags.length) return null;
  const h = draft.header;
  return (
    <ul className="border-t border-border">
      {draft.quote_flags.map((f) => {
        const blocking = f.severity === "block" && !draft.header_approved;
        return (
          <li key={f.code} className={`flex flex-wrap items-center gap-3 px-4 py-2.5 text-[13px] ${blocking ? "bg-attn-soft/60" : ""}`}>
            <FlagChip flag={f} approved={draft.header_approved} />
            <span className="min-w-0 flex-1 text-pretty">{f.message}</span>
            {f.rule_id && <RuleTag id={f.rule_id} title={ruleTitle(rules, f.rule_id)} />}
            {f.code === "incoterm_conflict" &&
              h.incoterms_mentioned
                .filter((t) => t !== h.incoterm)
                .map((t) => (
                  <Button key={t} onClick={() => apply({ incoterm: t }, "header")}>
                    Price as {t}
                  </Button>
                ))}
            {blocking && (
              <Button kind="accent" onClick={() => apply({ header_approved: true }, "header")}>
                <Check className="size-4" /> Keep {f.code === "incoterm_conflict" ? h.incoterm : "as is"}
              </Button>
            )}
            {f.severity === "block" && draft.header_approved && (
              <Button kind="ghost" onClick={() => apply({ header_approved: false }, "header")}>
                <Undo2 className="size-4" /> Reopen
              </Button>
            )}
          </li>
        );
      })}
    </ul>
  );
}
