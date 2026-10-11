"use client";

import { Search } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { GRADE_LABEL } from "@/lib/format";
import type { CatalogProduct } from "@/lib/types";

function score(p: CatalogProduct, q: string): number {
  if (!q) return 1;
  const hay = `${p.name} ${p.chemical} ${p.sku} ${p.cas}`.toLowerCase();
  const words = q.toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.every((w) => hay.includes(w))) return 0;
  return (p.sku.toLowerCase() === q.toLowerCase() || p.cas === q ? 10 : 0) + (p.name.toLowerCase().startsWith(words[0]) ? 2 : 1);
}

export function SkuPicker({
  products,
  onPick,
  onCancel,
  prefer = [],
  autoFocus = true,
}: {
  products: CatalogProduct[];
  onPick: (p: CatalogProduct) => void;
  onCancel?: () => void;
  prefer?: string[]; // chemical codes to list first (the match candidates)
  autoFocus?: boolean;
}) {
  const [q, setQ] = useState("");
  const [i, setI] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const list = useRef<HTMLUListElement>(null);

  const results = useMemo(() => {
    const rank = (p: CatalogProduct) => {
      const k = prefer.findIndex((c) => p.sku.startsWith(`QX-${c}-`));
      return k === -1 ? 99 : k;
    };
    return products
      .map((p) => ({ p, s: score(p, q.trim()) }))
      .filter((r) => r.s > 0)
      .sort((a, b) => b.s - a.s || (q ? 0 : rank(a.p) - rank(b.p)) || a.p.name.localeCompare(b.p.name))
      .slice(0, 40)
      .map((r) => r.p);
  }, [products, q, prefer]);

  useEffect(() => {
    if (autoFocus) input.current?.focus();
  }, [autoFocus]);
  useEffect(() => {
    list.current?.children[i]?.scrollIntoView({ block: "nearest" });
  }, [i]);

  return (
    <div className="overflow-hidden rounded-xl border border-border bg-surface shadow-pop">
      <div className="flex items-center gap-2 border-b border-border px-3">
        <Search className="size-4 text-faint" />
        <input
          ref={input}
          value={q}
          onChange={(e) => {
            setQ(e.target.value);
            setI(0);
          }}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") {
              e.preventDefault();
              setI((v) => Math.min(v + 1, results.length - 1));
            } else if (e.key === "ArrowUp") {
              e.preventDefault();
              setI((v) => Math.max(v - 1, 0));
            } else if (e.key === "Enter" && results[i]) {
              e.preventDefault();
              onPick(results[i]);
            } else if (e.key === "Escape") onCancel?.();
          }}
          placeholder="Search name, SKU or CAS number"
          className="h-10 flex-1 bg-transparent text-[13px] placeholder:text-faint focus:outline-none"
          aria-label="Search products"
        />
        {onCancel && (
          <button onClick={onCancel} className="text-[12px] text-muted hover:text-text">
            Esc
          </button>
        )}
      </div>
      <ul ref={list} role="listbox" className="max-h-[260px] overflow-y-auto p-1">
        {results.map((p, k) => (
          <li key={p.sku} role="option" aria-selected={k === i}>
            <button
              onMouseEnter={() => setI(k)}
              onClick={() => onPick(p)}
              className={`flex w-full items-center gap-3 rounded-lg px-2.5 py-1.5 text-left ${k === i ? "bg-surface-2" : ""}`}
            >
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[13px]">{p.chemical}</span>
                <span className="num block font-mono text-[11px] text-muted">
                  {p.sku} · CAS {p.cas}
                </span>
              </span>
              <span className="rounded bg-surface-3 px-1.5 py-px text-[11px] text-muted">{GRADE_LABEL[p.grade] ?? p.grade}</span>
              <span className="w-6 text-right text-[11px] text-faint">{p.uom}</span>
            </button>
          </li>
        ))}
        {!results.length && <li className="px-3 py-6 text-center text-[13px] text-muted">No product matches “{q}”.</li>}
      </ul>
    </div>
  );
}
