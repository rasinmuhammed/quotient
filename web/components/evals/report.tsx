import Link from "next/link";
import type { ReactNode } from "react";
import { type Acc, type EvalRun, FIELD_LABEL, RUNS, runLabel, type SplitSummary } from "@/lib/evals";
import { date, FLAG_LABEL, METHOD_LABEL, pct, seconds, usd } from "@/lib/format";
import type { FlagCode, MatchMethod } from "@/lib/types";
import { RfqTable } from "./rfq-table";

const SPLITS = ["dev", "holdout"] as const;

function Bar({ value }: { value: number | null }) {
  if (value === null) return null;
  const tone = value >= 0.98 ? "bg-accent" : value >= 0.9 ? "bg-accent/70" : "bg-attn";
  return (
    <span className="relative block h-1 w-full overflow-hidden rounded-full bg-surface-3">
      <span className={`absolute inset-y-0 left-0 rounded-full ${tone}`} style={{ width: `${Math.max(2, value * 100)}%` }} />
    </span>
  );
}

function AccCell({ a }: { a?: Acc }) {
  if (!a || a.accuracy === null) return <td className="px-3 py-2 text-right text-faint">–</td>;
  return (
    <td className="px-3 py-2">
      <div className="flex items-baseline justify-end gap-2">
        <span className="num font-medium">{pct(a.accuracy)}</span>
        <span className="num w-[52px] text-right text-[11.5px] text-faint">
          {a.correct}/{a.total}
        </span>
      </div>
      <div className="mt-1">
        <Bar value={a.accuracy} />
      </div>
    </td>
  );
}

function Section({ title, note, children }: { title: string; note?: ReactNode; children: ReactNode }) {
  return (
    <section className="mt-10">
      <div className="flex flex-wrap items-baseline gap-x-3">
        <h2 className="text-[15px] font-semibold">{title}</h2>
        {note && <p className="text-[12.5px] text-muted">{note}</p>}
      </div>
      <div className="mt-3 overflow-x-auto rounded-xl border border-border bg-surface shadow-card">{children}</div>
    </section>
  );
}

function SplitHead({ s }: { s: Partial<Record<string, SplitSummary>> }) {
  return (
    <>
      {SPLITS.map((k) => (
        <th key={k} className="w-[200px] px-3 py-2 text-right font-medium">
          {k === "dev" ? "Dev" : "Holdout"} <span className="num font-normal text-faint">({s[k]?.rfqs ?? 0} RFQs)</span>
        </th>
      ))}
    </>
  );
}

function Headline({ label, values, hint }: { label: string; values: [ReactNode, ReactNode]; hint?: string }) {
  return (
    <div className="bg-surface p-4">
      <div className="text-[12px] text-muted">{label}</div>
      <div className="mt-2 grid grid-cols-2 gap-2">
        {values.map((v, i) => (
          <div key={i}>
            <div className="num text-[20px] font-semibold tracking-[-0.02em]">{v}</div>
            <div className="text-[11px] text-faint">{i ? "holdout" : "dev"}</div>
          </div>
        ))}
      </div>
      {hint && <div className="mt-2 text-[11.5px] leading-snug text-faint">{hint}</div>}
    </div>
  );
}

export function EvalReport({ run }: { run: EvalRun }) {
  const s = run.splits;
  const dev = s.dev;
  const hold = s.holdout;
  const both = (f: (x: SplitSummary) => ReactNode): [ReactNode, ReactNode] => [dev ? f(dev) : "–", hold ? f(hold) : "–"];
  const methods = Array.from(new Set(SPLITS.flatMap((k) => Object.keys(s[k]?.match.by_method ?? {}))));
  const codes = Object.keys(s.all?.flags.by_code ?? {}).sort();

  return (
    <div className="mx-auto w-full max-w-[1180px] px-5 pt-10 pb-20 lg:px-8">
      <div className="max-w-[760px]">
        <h1 className="text-[30px] leading-tight font-semibold tracking-[-0.02em]">Evals</h1>
        <p className="mt-2 text-[14.5px] leading-relaxed text-muted text-pretty">
          30 messy RFQs in four formats, each with a gold answer whose prices were computed by the pricing engine, not
          typed by hand. Ten were held out before any prompt was written and are reported separately.
        </p>
      </div>

      <nav className="mt-6 flex flex-wrap gap-2" aria-label="Eval runs">
        {RUNS.map((r) => {
          const active = r.name === run.name;
          return (
            <Link
              key={r.name}
              href={`/evals/${r.name}`}
              className={`rounded-lg border px-3 py-1.5 text-[12.5px] transition-colors ${
                active ? "border-text bg-text text-bg" : "border-border bg-surface text-muted hover:text-text"
              }`}
            >
              <span className="font-medium">{runLabel(r)}</span>
              <span className={`num ml-2 ${active ? "opacity-70" : "text-faint"}`}>{date(r.run.at, { day: "numeric", month: "short" })}</span>
            </Link>
          );
        })}
      </nav>

      {run.run.oracle && (
        <div className="mt-4 rounded-xl border border-border bg-surface-2 px-4 py-3 text-[13px] text-muted">
          This run replaces the model with a scripted reader that returns the gold reading. It measures the deterministic
          stages alone (matching rules, grades, units, pricing, flags): anything below 100% here is a logic bug, not a
          reading error. Live model runs are measured against this ceiling.
        </div>
      )}

      <div className="mt-6 grid gap-px overflow-hidden rounded-xl border border-border bg-border sm:grid-cols-2 lg:grid-cols-4">
        <Headline label="Right product (SKU)" values={both((x) => pct(x.match.overall.accuracy))} hint="of all gold lines, including lines with no match" />
        <Headline label="Exact price" values={both((x) => pct(x.price.lines.accuracy))} hint="unit price and line total equal to the gold, to the cent" />
        <Headline
          label="Flags: recall / precision"
          values={both((x) => (
            <span className="text-[17px] whitespace-nowrap">
              {pct(x.flags.recall, 0)}
              <span className="text-faint"> / </span>
              {pct(x.flags.precision, 0)}
            </span>
          ))}
          hint="every flag on every line and quote, by code"
        />
        <Headline
          label="Silent errors"
          values={both((x) => (
            <span className={x.routing.silent_errors ? "text-bad" : "text-accent"}>{x.routing.silent_errors}</span>
          ))}
          hint="lines wrong but not sent to a person: the number that matters most"
        />
        <Headline label="Lines needing a person, caught" values={both((x) => pct(x.routing.lines_needing_a_person.recall))} />
        <Headline label="Auto-quoted lines correct" values={both((x) => pct(x.routing.auto_quoted_lines.accuracy))} hint="lines the system would send without asking" />
        <Headline
          label="Time per RFQ, median / p90"
          values={both((x) => (
            <span className="text-[17px] whitespace-nowrap">
              {seconds(x.time.median_active_s)}
              <span className="text-faint"> / </span>
              {seconds(x.time.p90_active_s)}
            </span>
          ))}
          hint="end to end, excluding rate-limit waits"
        />
        <Headline label="Model cost per RFQ" values={both((x) => usd(x.cost.mean_usd))} hint="mean, at list token prices, cached input discounted" />
      </div>

      <Section title="Reading: accuracy per field" note="Line fields over gold lines; a missed line counts as wrong. Header fields per RFQ.">
        <table className="w-full text-[13px]">
          <thead className="border-b border-border text-[12px] text-muted">
            <tr>
              <th className="px-4 py-2 text-left font-medium">Field</th>
              <SplitHead s={s} />
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {Object.keys(FIELD_LABEL).map((f) => (
              <tr key={f}>
                <td className="px-4 py-2">{FIELD_LABEL[f]}</td>
                {SPLITS.map((k) => (
                  <AccCell key={k} a={s[k]?.extraction[f]} />
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </Section>

      <Section title="Matching: right SKU, by how it was matched" note="Accuracy within each method is that method's precision.">
        <table className="w-full text-[13px]">
          <thead className="border-b border-border text-[12px] text-muted">
            <tr>
              <th className="px-4 py-2 text-left font-medium">Method</th>
              <SplitHead s={s} />
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            <tr className="bg-surface-2/50 font-medium">
              <td className="px-4 py-2">All lines</td>
              {SPLITS.map((k) => (
                <AccCell key={k} a={s[k]?.match.overall} />
              ))}
            </tr>
            {methods.map((m) => (
              <tr key={m}>
                <td className="px-4 py-2">{METHOD_LABEL[m as MatchMethod] ?? (m === "missed" ? "Line not found" : m)}</td>
                {SPLITS.map((k) => (
                  <AccCell key={k} a={s[k]?.match.by_method[m]} />
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </Section>

      <Section title="Flags by code" note="All 30 RFQs. A flag is right when the same code is on the same line (or quote) as in the gold.">
        <table className="w-full text-[13px]">
          <thead className="border-b border-border text-[12px] text-muted">
            <tr>
              <th className="px-4 py-2 text-left font-medium">Flag</th>
              <th className="px-3 py-2 text-right font-medium">Gold</th>
              <th className="px-3 py-2 text-right font-medium">Caught</th>
              <th className="px-3 py-2 text-right font-medium">Missed</th>
              <th className="px-3 py-2 text-right font-medium">Extra</th>
              <th className="px-3 py-2 text-right font-medium">Recall</th>
              <th className="px-4 py-2 text-right font-medium">Precision</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {codes.map((c) => {
              const v = s.all!.flags.by_code[c];
              return (
                <tr key={c}>
                  <td className="px-4 py-2">
                    {FLAG_LABEL[c as FlagCode] ?? c} <span className="ml-1 font-mono text-[11px] text-faint">{c}</span>
                  </td>
                  <td className="num px-3 py-2 text-right">{v.tp + v.fn}</td>
                  <td className="num px-3 py-2 text-right">{v.tp}</td>
                  <td className={`num px-3 py-2 text-right ${v.fn ? "text-bad" : "text-faint"}`}>{v.fn}</td>
                  <td className={`num px-3 py-2 text-right ${v.fp ? "text-attn" : "text-faint"}`}>{v.fp}</td>
                  <td className="num px-3 py-2 text-right">{pct(v.recall, 0)}</td>
                  <td className="num px-4 py-2 text-right">{pct(v.precision, 0)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </Section>

      <Section
        title="Every RFQ"
        note={
          <>
            Open a row for line-by-line differences. Run {run.name} · code {run.run.code} · rules {run.run.rules_version} · data{" "}
            {run.run.data_version}
          </>
        }
      >
        <RfqTable rfqs={run.rfqs} holdout={run.run.holdout_ids} />
      </Section>

      {!run.run.oracle && s.all && (
        <p className="mt-6 text-[12px] text-faint">
          {s.all.cost.calls_per_rfq} model calls per RFQ · {s.all.cost.prompt_tokens.toLocaleString()} prompt tokens (
          {pct(s.all.cost.cached_share, 0)} cached) · {s.all.cost.completion_tokens.toLocaleString()} completion tokens, of which{" "}
          {s.all.cost.reasoning_tokens.toLocaleString()} reasoning · total {usd(s.all.cost.total_usd)} for the run · email drafts:{" "}
          {Object.entries(s.all.email)
            .map(([k, v]) => `${v} ${k === "llm" ? "model-written" : "template"}`)
            .join(", ")}
        </p>
      )}
    </div>
  );
}
