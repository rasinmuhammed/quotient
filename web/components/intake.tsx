"use client";

import { ArrowRight, ClipboardPaste, FileUp, LoaderCircle, Play } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { SAMPLES } from "@/lib/demo";
import { FORMAT_LABEL } from "@/lib/format";
import { saveDraft } from "@/lib/store";
import { FormatIcon } from "./ui";

const ACCEPT = ".eml,.pdf,.xlsx,.xlsm,.txt";

export function Intake() {
  const router = useRouter();
  const [mode, setMode] = useState<"file" | "paste">("file");
  const [text, setText] = useState("");
  const [drag, setDrag] = useState(false);
  const [busy, setBusy] = useState<{ label: string; started: number } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);

  async function run(label: string, payload: { file?: File; text?: string }) {
    setError(null);
    setBusy({ label, started: performance.now() });
    try {
      const { draft, replay } = await api.quote(payload);
      saveDraft({ draft, replay, sample: null });
      router.push(`/review?id=${encodeURIComponent(draft.id)}`);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
      setBusy(null);
    }
  }

  function pick(files: FileList | null) {
    const f = files?.[0];
    if (f) run(f.name, { file: f });
  }

  return (
    <div className="rise mt-9 grid gap-5 [animation-delay:60ms] lg:grid-cols-[minmax(0,1.05fr)_minmax(0,1fr)]">
      <section className="flex flex-col rounded-2xl border border-border bg-surface shadow-card">
        <div className="flex items-center gap-1 border-b border-border p-1.5">
          {(["file", "paste"] as const).map((m) => (
            <button
              key={m}
              onClick={() => setMode(m)}
              className={`flex items-center gap-2 rounded-lg px-3 py-1.5 text-[13px] font-medium transition-colors ${
                mode === m ? "bg-surface-2 text-text" : "text-muted hover:text-text"
              }`}
            >
              {m === "file" ? <FileUp className="size-4" /> : <ClipboardPaste className="size-4" />}
              {m === "file" ? "Upload a file" : "Paste text"}
            </button>
          ))}
        </div>

        {busy ? (
          <Working label={busy.label} started={busy.started} />
        ) : mode === "file" ? (
          <button
            onClick={() => input.current?.click()}
            onDragOver={(e) => {
              e.preventDefault();
              setDrag(true);
            }}
            onDragLeave={() => setDrag(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDrag(false);
              pick(e.dataTransfer.files);
            }}
            className={`group m-4 flex min-h-[260px] flex-1 flex-col items-center justify-center rounded-xl border border-dashed text-center transition-colors ${
              drag ? "border-accent bg-accent-soft" : "border-border-strong hover:border-accent hover:bg-surface-2"
            }`}
          >
            <span className="grid size-11 place-items-center rounded-xl bg-surface-2 text-muted transition-colors group-hover:text-accent">
              <FileUp className="size-5" />
            </span>
            <span className="mt-4 text-[15px] font-medium">Drop an RFQ here, or choose a file</span>
            <span className="mt-1.5 text-[13px] text-muted">Email (.eml), PDF, Excel (.xlsx) or text · up to 5 MB</span>
            <input ref={input} type="file" accept={ACCEPT} className="hidden" onChange={(e) => pick(e.target.files)} />
          </button>
        ) : (
          <form
            className="m-4 flex flex-1 flex-col gap-3"
            onSubmit={(e) => {
              e.preventDefault();
              if (text.trim()) run("pasted text", { text });
            }}
          >
            <textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && text.trim()) run("pasted text", { text });
              }}
              placeholder={"Paste the customer's email, including the From line if you have it.\n\nHi, please quote 12 drums of MEK and 4 MT of IPA, CIF Rotterdam, delivery in November…"}
              className="min-h-[220px] flex-1 resize-none rounded-xl border border-border bg-surface-2/60 p-4 text-[13.5px] leading-relaxed placeholder:text-faint focus:border-accent focus:bg-surface focus:outline-none"
            />
            <div className="flex items-center justify-between">
              <span className="text-[12px] text-faint">⌘ Enter to submit</span>
              <button
                disabled={!text.trim()}
                className="inline-flex items-center gap-2 rounded-lg bg-text px-3.5 py-2 text-[13px] font-medium text-bg transition-opacity disabled:opacity-30"
              >
                Quote this <ArrowRight className="size-4" />
              </button>
            </div>
          </form>
        )}

        {error && (
          <div className="mx-4 mb-4 rounded-lg border border-bad/30 bg-bad-soft px-3.5 py-2.5 text-[13px] text-bad">{error}</div>
        )}
      </section>

      <Samples />
    </div>
  );
}

function Working({ label, started }: { label: string; started: number }) {
  const [now, setNow] = useState(started);
  useEffect(() => {
    const t = setInterval(() => setNow(performance.now()), 100);
    return () => clearInterval(t);
  }, []);
  const s = (now - started) / 1000;
  return (
    <div className="m-4 flex min-h-[260px] flex-1 flex-col items-center justify-center rounded-xl bg-surface-2/60 text-center">
      <LoaderCircle className="size-6 animate-spin text-accent" />
      <div className="mt-4 text-[15px] font-medium">Reading {label}</div>
      <div className="num mt-1 text-[13px] text-muted">{s.toFixed(1)} s</div>
      <p className="mt-5 max-w-[340px] text-[12.5px] leading-relaxed text-faint">
        The model reads the document; matching, pricing and checks run in code. Usually 5–20 s, plus ~30 s if the
        server was asleep and up to a minute more when the model’s free tier is rate-limited.
      </p>
    </div>
  );
}

const FILTERS = ["all", "eml", "pdf", "xlsx", "txt"] as const;

function Samples() {
  const [filter, setFilter] = useState<(typeof FILTERS)[number]>("all");
  const shown = useMemo(() => SAMPLES.filter((s) => filter === "all" || s.format === filter), [filter]);

  return (
    <section className="flex min-h-0 flex-col rounded-2xl border border-border bg-surface shadow-card">
      <div className="flex items-center gap-3 border-b border-border px-4 py-3">
        <div>
          <h2 className="text-[14px] font-semibold">Sample RFQs</h2>
          <p className="text-[12px] text-muted">The 30 eval RFQs: messy wording, mixed formats, traps.</p>
        </div>
        <div className="ml-auto flex gap-0.5 rounded-lg bg-surface-2 p-0.5">
          {FILTERS.map((f) => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={`rounded-md px-2 py-1 text-[12px] font-medium transition-colors ${
                filter === f ? "bg-surface text-text shadow-card" : "text-muted hover:text-text"
              }`}
            >
              {f === "all" ? "All" : FORMAT_LABEL[f]}
            </button>
          ))}
        </div>
      </div>
      <ul className="max-h-[380px] min-h-[260px] flex-1 overflow-y-auto p-1.5">
        {shown.map((s) => (
          <li key={s.id}>
            <Link
              href={`/review?sample=${s.id}`}
              className="group flex items-center gap-3 rounded-lg px-2.5 py-2 transition-colors hover:bg-surface-2"
            >
              <FormatIcon format={s.format} />
              <div className="min-w-0 flex-1">
                <div className="truncate text-[13px] font-medium">{s.title}</div>
                <div className="mt-0.5 flex items-center gap-2 text-[12px] text-muted">
                  <span className="num font-mono text-[11.5px]">{s.id}</span>
                  <span className="text-faint">·</span>
                  <span>
                    {s.lines} line{s.lines === 1 ? "" : "s"}
                  </span>
                  {s.split === "holdout" && (
                    <span className="rounded bg-surface-3 px-1.5 text-[11px] text-muted" title="Held out: never used while writing prompts">
                      holdout
                    </span>
                  )}
                </div>
              </div>
              <span
                className="flex items-center gap-1 text-[11.5px] text-faint opacity-0 transition-opacity group-hover:opacity-100"
                title={s.replay ? `Opens what ${s.replay.model} produced in the eval run; you can run it live from there` : "Runs the model live"}
              >
                {s.replay ? "saved run" : "live"} <Play className="size-3" />
              </span>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}
