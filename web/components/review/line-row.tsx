"use client";

import { Check, ChevronRight, CircleCheck, LoaderCircle, Pencil, Trash2, TriangleAlert, Undo2, X } from "lucide-react";
import { useState } from "react";
import { GRADE_LABEL, METHOD_LABEL, money, qty, unitMoney } from "@/lib/format";
import type { LineResult, Spanned } from "@/lib/types";
import { Button, FlagChip, Label, RuleTag } from "../ui";
import { ruleTitle, useReview } from "./context";
import { SkuPicker } from "./sku-picker";

export const LINE_GRID =
  "grid grid-cols-[22px_minmax(0,1.2fr)_minmax(0,1.45fr)_minmax(0,0.95fr)_minmax(64px,0.55fr)_minmax(84px,0.7fr)] items-start gap-x-4";

const GRADE_SOURCE: Record<string, string> = {
  line: "grade as written",
  document: "grade stated for the whole RFQ",
  only_grade: "the only grade offered",
  default: "no grade stated: lowest grade assumed",
  manual: "chosen by you",
  none: "",
};

export function LineRow({ line }: { line: LineResult }) {
  const { draft, activeLine, setActiveLine, openLine, setOpenLine, busy } = useReview();
  const open = openLine === line.line_no;
  const p = line.priced;
  const x = line.extracted;
  const cur = draft.currency;
  const blocking = line.flags.filter((f) => f.severity === "block");
  const pending = busy === `line-${line.line_no}`;

  return (
    <li
      id={`line-${line.line_no}`}
      onMouseEnter={() => setActiveLine(line.line_no)}
      onMouseLeave={() => setActiveLine(null)}
      className={`scroll-mt-20 border-b border-border last:border-b-0 transition-colors ${
        activeLine === line.line_no && !open ? "bg-surface-2/60" : ""
      }`}
    >
      <button onClick={() => setOpenLine(open ? null : line.line_no)} className={`${LINE_GRID} w-full px-4 py-3.5 text-left`} aria-expanded={open}>
        <span className="pt-0.5">
          {pending ? (
            <LoaderCircle className="size-4 animate-spin text-faint" />
          ) : line.needs_human ? (
            <TriangleAlert className="size-4 text-attn" aria-label="needs you" />
          ) : line.approved ? (
            <CircleCheck className="size-4 text-accent" aria-label="approved by you" />
          ) : (
            <Check className="size-4 text-faint" aria-label="ready" />
          )}
        </span>

        <span className="min-w-0">
          <span className="block truncate text-[13.5px] font-medium">{x?.mention.value ?? "Added by you"}</span>
          <span className="block truncate text-[12px] text-muted">
            {x ? [x.quantity.value, x.unit.value].filter(Boolean).join(" ") || "no quantity given" : "–"}
          </span>
        </span>

        <span className="min-w-0">
          {line.match.sku ? (
            <>
              <span className="block truncate text-[13.5px]">{p?.product_name ?? line.match.sku}</span>
              <span className="mt-0.5 flex items-center gap-1.5 text-[12px] text-muted">
                <span className="num font-mono text-[11px]">{line.match.sku}</span>
                <span className="text-faint">·</span>
                <span className="truncate">{METHOD_LABEL[line.match.method]}</span>
              </span>
            </>
          ) : (
            <span className="text-[13px] text-attn">No product matched</span>
          )}
        </span>

        <span className="min-w-0 text-right">
          {p ? (
            <>
              <span className="num block text-[13.5px]">
                {qty(p.quoted_qty)} {p.uom}
              </span>
              <span className="block truncate text-[12px] text-muted">
                {p.packs} × {p.pack_description}
              </span>
            </>
          ) : (
            <span className="text-[13px] text-faint">–</span>
          )}
        </span>

        <span className="num text-right text-[13.5px]">
          {p?.unit_price ? unitMoney(p.unit_price, cur) : "–"}
          {p?.unit_price && <span className="block text-[11.5px] text-faint">per {p.uom}</span>}
        </span>

        <span className="flex items-start justify-end gap-2">
          <span className={`num text-right text-[13.5px] font-medium ${p?.line_total ? "" : "text-faint"}`}>
            {p?.line_total ? money(p.line_total, cur) : "–"}
          </span>
          <ChevronRight className={`mt-0.5 size-4 shrink-0 text-faint transition-transform ${open ? "rotate-90" : ""}`} />
        </span>

        {line.flags.length > 0 && (
          <span className="col-start-2 col-end-7 mt-2 flex flex-wrap gap-1.5">
            {line.flags.map((f) => (
              <FlagChip key={f.code} flag={f} approved={line.approved} />
            ))}
          </span>
        )}
      </button>

      {open && <LineDetail line={line} blocking={blocking.length > 0} />}
    </li>
  );
}

function Read({ label, s, children }: { label: string; s?: Spanned; children?: React.ReactNode }) {
  const v = children ?? s?.value;
  return (
    <div className="flex items-baseline gap-3 py-1">
      <span className="w-[86px] shrink-0 text-[12px] text-muted">{label}</span>
      <span className="min-w-0 flex-1 text-[13px]">
        {v ?? <span className="text-faint">–</span>}
        {s && s.value !== null && !s.verified && (
          <span className="ml-2 rounded bg-attn-soft px-1 text-[11px] text-attn">not found in the document</span>
        )}
      </span>
    </div>
  );
}

function LineDetail({ line, blocking }: { line: LineResult; blocking: boolean }) {
  const { catalog, rules, apply } = useReview();
  const [picking, setPicking] = useState(false);
  const [editQty, setEditQty] = useState<string | null>(null);
  const p = line.priced;
  const x = line.extracted;
  const m = line.match;
  const product = catalog?.products.find((c) => c.sku === m.sku);
  const key = `line-${line.line_no}`;

  return (
    <div className="rise border-t border-dashed border-border bg-surface-2/40 px-4 pt-4 pb-5">
      <div className="grid gap-6 lg:grid-cols-2">
        <div className="space-y-5">
          {x && (
            <div>
              <Label>Read from the document</Label>
              <div className="mt-1.5">
                <Read label="Product" s={x.mention} />
                {x.english_name && <Read label="Read as">{x.english_name}</Read>}
                <Read label="CAS" s={x.cas} />
                <Read label="Grade" s={x.grade} />
                <Read label="Quantity" s={x.quantity} />
                <Read label="Unit" s={x.unit} />
                <Read label="Packaging" s={x.packaging} />
                <Read label="Confidence">
                  <span className={`num ${x.confidence < 0.6 ? "text-attn" : ""}`}>{x.confidence.toFixed(2)}</span>
                </Read>
                {x.note && <Read label="Reader note">{x.note}</Read>}
              </div>
            </div>
          )}

          <div>
            <div className="flex items-center justify-between">
              <Label>Match</Label>
              {catalog && !picking && (
                <button onClick={() => setPicking(true)} className="inline-flex items-center gap-1 text-[12px] font-medium text-accent hover:text-accent-strong">
                  <Pencil className="size-3" /> {m.sku ? "Change product" : "Choose product"}
                </button>
              )}
            </div>
            {picking && catalog ? (
              <div className="mt-2">
                <SkuPicker
                  products={catalog.products}
                  prefer={m.candidates.map((c) => c.chemical_code)}
                  onCancel={() => setPicking(false)}
                  onPick={(prod) => {
                    setPicking(false);
                    apply({ lines: [{ line_no: line.line_no, sku: prod.sku }] }, key);
                  }}
                />
              </div>
            ) : (
              <div className="mt-1.5 rounded-lg border border-border bg-surface p-3">
                <div className="flex items-center gap-2">
                  <span className="rounded-md bg-accent-soft px-1.5 py-px text-[11.5px] font-medium text-accent">{METHOD_LABEL[m.method]}</span>
                  {m.score !== null && m.method !== "manual" && <span className="num text-[11.5px] text-faint">score {Math.round(m.score)}</span>}
                </div>
                <p className="mt-2 text-[13px] text-pretty">{m.explanation || "–"}</p>
                {m.sku && (
                  <p className="mt-1 text-[12px] text-muted">
                    {GRADE_LABEL[m.grade ?? ""] ?? m.grade}: {GRADE_SOURCE[m.grade_source]}
                  </p>
                )}
                {m.candidates.length > 0 && (
                  <div className="mt-3 border-t border-border pt-2">
                    <div className="text-[11.5px] text-faint">Closest catalog products</div>
                    <ul className="mt-1 space-y-0.5">
                      {m.candidates.map((c) => (
                        <li key={c.chemical_code} className="flex items-center justify-between text-[12.5px]">
                          <span className={c.chemical_code === m.chemical_code ? "font-medium" : "text-muted"}>{c.name}</span>
                          <span className="num text-[11.5px] text-faint">{Math.round(c.score)}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            )}
          </div>

          <div>
            <Label>Quantity</Label>
            <div className="mt-1.5 space-y-1 text-[13px]">
              {line.quantity.steps.map((s, i) => (
                <div key={i} className="num text-muted">
                  {s}
                </div>
              ))}
              {editQty !== null ? (
                <form
                  className="flex items-center gap-2 pt-1"
                  onSubmit={(e) => {
                    e.preventDefault();
                    const v = editQty.trim().replace(/,/g, "");
                    if (/^\d+(\.\d+)?$/.test(v) && Number(v) > 0) {
                      setEditQty(null);
                      apply({ lines: [{ line_no: line.line_no, quantity: v }] }, key);
                    }
                  }}
                >
                  <input
                    autoFocus
                    value={editQty}
                    onChange={(e) => setEditQty(e.target.value)}
                    onKeyDown={(e) => e.key === "Escape" && setEditQty(null)}
                    inputMode="decimal"
                    className="num w-32 rounded-md border border-border-strong bg-surface px-2 py-1 text-[13px] focus:border-accent focus:outline-none"
                  />
                  <span className="text-muted">{product?.uom ?? line.quantity.uom ?? ""}</span>
                  <Button type="submit" kind="accent">
                    Reprice
                  </Button>
                  <button type="button" onClick={() => setEditQty(null)} className="p-1 text-muted hover:text-text" aria-label="Cancel">
                    <X className="size-4" />
                  </button>
                </form>
              ) : (
                m.sku && (
                  <div className="flex flex-wrap items-center gap-2 pt-1">
                    <span>
                      {line.quantity.value ? (
                        <>
                          Requested <span className="num font-medium">{qty(line.quantity.value)} {line.quantity.uom}</span>
                        </>
                      ) : (
                        <span className="text-attn">No quantity yet</span>
                      )}
                    </span>
                    <button onClick={() => setEditQty(line.quantity.value ?? "")} className="text-[12px] font-medium text-accent hover:text-accent-strong">
                      Edit
                    </button>
                  </div>
                )
              )}
              {product && p && (
                <div className="flex items-center gap-2 pt-1">
                  <span className="text-muted">Pack</span>
                  <select
                    value={p.pack_code}
                    onChange={(e) => apply({ lines: [{ line_no: line.line_no, pack_code: e.target.value }] }, key)}
                    className="rounded-md border border-border bg-surface px-2 py-1 text-[13px] focus:border-accent focus:outline-none"
                  >
                    {product.packs.map((code) => (
                      <option key={code} value={code}>
                        {catalog?.packs[code]?.description ?? code}
                      </option>
                    ))}
                  </select>
                  <span className="num text-muted">
                    → {p.packs} packs, {qty(p.quoted_qty)} {p.uom} quoted
                  </span>
                </div>
              )}
            </div>
          </div>
        </div>

        <div className="space-y-5">
          <div>
            <Label>Price build-up</Label>
            {p ? (
              <ol className="mt-1.5 overflow-hidden rounded-lg border border-border bg-surface">
                {p.steps.map((s, i) => {
                  const last = i >= p.steps.length - 2;
                  return (
                    <li key={i} className={`flex items-start gap-3 px-3 py-2 text-[12.5px] ${i ? "border-t border-border" : ""} ${last ? "bg-surface-2/60" : ""}`}>
                      <span className="min-w-0 flex-1">
                        <span className={last ? "font-medium" : ""}>{s.label}</span>
                        <span className="mt-0.5 flex flex-wrap items-center gap-1.5">
                          <RuleTag id={s.rule_id} title={ruleTitle(rules, s.rule_id)} />
                          {s.source && <span className="num font-mono text-[10.5px] text-accent">{s.source}</span>}
                        </span>
                      </span>
                      {s.value !== null && (
                        <span className={`num shrink-0 text-right ${last ? "font-medium" : "text-muted"}`}>
                          {qty(s.value)} <span className="text-[11px] text-faint">{s.unit}</span>
                        </span>
                      )}
                    </li>
                  );
                })}
              </ol>
            ) : (
              <p className="mt-1.5 text-[13px] text-muted">Not priced: the line needs a product and a quantity first.</p>
            )}
          </div>

          {line.flags.length > 0 && (
            <div>
              <Label>Flags</Label>
              <ul className="mt-1.5 space-y-2">
                {line.flags.map((f) => (
                  <li key={f.code} className="flex items-start gap-2.5 text-[13px]">
                    <FlagChip flag={f} approved={line.approved} />
                    <span className="min-w-0 flex-1 text-pretty text-muted">
                      {f.message}
                      {f.rule_id && (
                        <span className="ml-1.5 inline-block align-middle">
                          <RuleTag id={f.rule_id} title={ruleTitle(rules, f.rule_id)} />
                        </span>
                      )}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div className="flex flex-wrap items-center gap-2 pt-1">
            {blocking && !line.approved && (
              <Button
                kind="accent"
                onClick={() => apply({ lines: [{ line_no: line.line_no, approved: true }] }, key)}
                title={p?.line_total ? "Accept the flags and quote this line as priced" : "The reply will tell the customer this item is confirmed separately"}
              >
                <Check className="size-4" /> {p?.line_total ? "Approve line" : "Quote without it"}
              </Button>
            )}
            {line.approved && (
              <Button onClick={() => apply({ lines: [{ line_no: line.line_no, approved: false }] }, key)}>
                <Undo2 className="size-4" /> Reopen
              </Button>
            )}
            <Button kind="ghost" onClick={() => apply({ lines: [{ line_no: line.line_no, remove: true }] }, key)}>
              <Trash2 className="size-4" /> Remove line
            </Button>
            {p && (
              <span className="num ml-auto text-[11.5px] text-faint">
                {p.price_row_id} · {p.fx_row_id} @ {qty(p.fx_rate)} ({p.fx_snapshot_date}) · lead {p.lead_time_days} d
              </span>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
