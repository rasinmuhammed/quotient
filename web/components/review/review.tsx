"use client";

import { ArrowLeft, LoaderCircle, Play, Plus, TriangleAlert } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { seconds, usd } from "@/lib/format";
import { loadDraft, saveDraft } from "@/lib/store";
import type { CatalogData, Edits, QuoteDraft, RulesData } from "@/lib/types";
import { Button } from "../ui";
import { ReviewContext, type ReviewState, useReview } from "./context";
import { DocumentPane } from "./document-pane";
import { EmailPanel } from "./email-panel";
import { LINE_GRID, LineRow } from "./line-row";
import { SkuPicker } from "./sku-picker";
import { Summary } from "./summary";

type Load = { kind: "loading"; live: boolean } | { kind: "error"; message: string } | { kind: "ready" };

export function Review() {
  const params = useSearchParams();
  const id = params.get("id");
  const sample = params.get("sample");
  const live = params.get("live") === "1";
  // A new id or sample is a new review: remount instead of resetting state by hand.
  return <ReviewLoader key={`${id}|${sample}|${live}`} id={id} sample={sample} wantLive={live} />;
}

const LOST = "This draft was opened in another tab or session. Drafts live in the browser tab that made them.";

function ReviewLoader({ id, sample, wantLive }: { id: string | null; sample: string | null; wantLive: boolean }) {
  const router = useRouter();
  const [stored] = useState(() => (id ? loadDraft(id) : null));
  const [load, setLoad] = useState<Load>(() =>
    id ? (stored ? { kind: "ready" } : { kind: "error", message: LOST }) : sample ? { kind: "loading", live: wantLive } : { kind: "error", message: "Nothing to review yet." },
  );
  const [draft, setDraft] = useState<QuoteDraft | null>(stored?.draft ?? null);
  const [replay, setReplay] = useState<string | null>(stored?.replay ?? null);
  const [catalog, setCatalog] = useState<CatalogData | null>(null);
  const [rules, setRules] = useState<RulesData | null>(null);
  const [live, setLive] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [editError, setEditError] = useState<string | null>(null);
  const [activeLine, setActiveLine] = useState<number | null>(null);
  const [openLine, setOpenLine] = useState<number | null>(null);

  useEffect(() => {
    let alive = true;
    api.catalog().then((c) => alive && setCatalog(c)).catch(() => {});
    api.rules().then((r) => alive && setRules(r)).catch(() => {});
    api.health().then((h) => alive && setLive(h.live)).catch(() => {});
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    if (id || !sample) return;
    let alive = true;
    api
      .quote({ sample, live: wantLive })
      .then(({ draft, replay }) => {
        if (!alive) return;
        setDraft(draft);
        setReplay(replay);
        saveDraft({ draft, replay, sample });
        setLoad({ kind: "ready" });
      })
      .catch((e) => alive && setLoad({ kind: "error", message: e instanceof ApiError ? e.message : String(e) }));
    return () => {
      alive = false;
    };
  }, [id, sample, wantLive]);

  const update = useCallback(
    (d: QuoteDraft) => {
      setDraft(d);
      saveDraft({ draft: d, replay, sample });
    },
    [replay, sample],
  );

  const apply = useCallback(
    async (edits: Edits, key = "draft") => {
      if (!draft) return;
      setBusy(key);
      setEditError(null);
      try {
        update(await api.reprice(draft, edits));
      } catch (e) {
        setEditError(e instanceof ApiError ? e.message : String(e));
      } finally {
        setBusy(null);
      }
    },
    [draft, update],
  );

  const state = useMemo<ReviewState | null>(
    () =>
      draft && {
        draft,
        catalog,
        rules,
        live,
        apply,
        busy,
        activeLine,
        setActiveLine,
        openLine,
        setOpenLine,
      },
    [draft, catalog, rules, live, apply, busy, activeLine, openLine],
  );

  if (load.kind === "loading") return <Loading live={load.live} sample={sample} />;
  if (load.kind === "error" || !state || !draft)
    return (
      <div className="mx-auto mt-24 max-w-[460px] px-5 text-center">
        <TriangleAlert className="mx-auto size-6 text-attn" />
        <p className="mt-4 text-[15px] font-medium">{load.kind === "error" ? load.message : "Something went wrong."}</p>
        <Link href="/" className="mt-6 inline-flex items-center gap-1.5 text-[13px] font-medium text-accent">
          <ArrowLeft className="size-4" /> Start a new quote
        </Link>
      </div>
    );

  return (
    <ReviewContext.Provider value={state}>
      <div className="mx-auto w-full max-w-[1440px] px-5 pt-5 pb-16 lg:px-8">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <Link href="/" className="rounded-md p-1 text-muted hover:bg-surface-2 hover:text-text" aria-label="Back">
            <ArrowLeft className="size-4" />
          </Link>
          <h1 className="num font-mono text-[14px] font-medium">{draft.id}</h1>
          <span className="text-[13px] text-muted">{draft.document.source_name}</span>
          {replay && (
            <span
              className="rounded-full border border-border bg-surface px-2 py-0.5 text-[11.5px] text-muted"
              title={`Opened from the stored output of eval run ${replay}; repriced now with the current rules.`}
            >
              Replay of a live run
            </span>
          )}
          <div className="ml-auto flex items-center gap-2">
            {busy && <LoaderCircle className="size-4 animate-spin text-faint" />}
            {sample && live && (
              <Button kind="ghost" onClick={() => router.push(`/review?sample=${sample}&live=1`)} title="Read this RFQ again with the live model">
                <Play className="size-4" /> Run live
              </Button>
            )}
          </div>
        </div>

        {editError && <div className="mt-3 rounded-lg border border-bad/30 bg-bad-soft px-3.5 py-2 text-[13px] text-bad">{editError}</div>}

        <div className="rise mt-4">
          <Summary />
        </div>

        <div className="mt-5 grid items-start gap-5 xl:grid-cols-[minmax(0,0.82fr)_minmax(0,1.5fr)]">
          <div className="xl:sticky xl:top-[72px] xl:h-[calc(100vh-96px)]">
            <DocumentPane />
          </div>
          <div className="min-w-0 space-y-5">
            <Lines />
            <EmailPanel onDraft={update} />
            <Meta draft={draft} />
          </div>
        </div>
      </div>
    </ReviewContext.Provider>
  );
}

function Lines() {
  const { draft, catalog, apply } = useReview();
  const [adding, setAdding] = useState(false);
  return (
    <section className="overflow-hidden rounded-2xl border border-border bg-surface shadow-card">
      <div className={`${LINE_GRID} border-b border-border px-4 py-2 text-[11.5px] font-medium text-faint`}>
        <span />
        <span>Customer wrote</span>
        <span>Matched product</span>
        <span className="text-right">Quantity</span>
        <span className="text-right">Unit price</span>
        <span className="pr-6 text-right">Total {draft.currency}</span>
      </div>
      <ul>
        {draft.lines.map((l) => (
          <LineRow key={l.line_no} line={l} />
        ))}
        {!draft.lines.length && <li className="px-4 py-8 text-center text-[13px] text-muted">No product lines were found in this RFQ.</li>}
      </ul>
      <div className="border-t border-border px-4 py-2.5">
        {adding && catalog ? (
          <div className="max-w-[520px] py-1">
            <SkuPicker
              products={catalog.products}
              onCancel={() => setAdding(false)}
              onPick={(p) => {
                setAdding(false);
                apply({ lines: [{ line_no: null, sku: p.sku }] });
              }}
            />
          </div>
        ) : (
          <button onClick={() => setAdding(true)} disabled={!catalog} className="inline-flex items-center gap-1.5 text-[12.5px] font-medium text-muted hover:text-text disabled:opacity-40">
            <Plus className="size-4" /> Add a line
          </button>
        )}
      </div>
    </section>
  );
}

function Meta({ draft }: { draft: QuoteDraft }) {
  const u = draft.llm;
  const total = draft.timings.reduce((a, t) => a + t.seconds, 0);
  return (
    <div className="flex flex-wrap gap-x-5 gap-y-1 px-1 text-[11.5px] text-faint">
      <span>
        Rules <span className="num font-mono">{draft.rules_version}</span>
      </span>
      <span>
        Data <span className="num font-mono">{draft.data_version}</span>
      </span>
      <span>Model {draft.model.replace(/^openai\//, "")}</span>
      {u.calls > 0 && (
        <span className="num">
          {u.calls} model call{u.calls === 1 ? "" : "s"} · {(u.prompt_tokens + u.completion_tokens).toLocaleString()} tokens
          {u.prompt_tokens ? ` (${Math.round((100 * u.cached_tokens) / u.prompt_tokens)}% cached)` : ""} · {usd(u.cost_usd)}
        </span>
      )}
      {total > 0 && (
        <span className="num" title={draft.timings.map((t) => `${t.stage} ${seconds(t.seconds)}`).join(" · ")}>
          {seconds(total)} end to end{u.waited_seconds ? `, ${seconds(u.waited_seconds)} rate-limited` : ""}
        </span>
      )}
    </div>
  );
}

function Loading({ live, sample }: { live: boolean; sample: string | null }) {
  const [t0] = useState(() => performance.now());
  const [now, setNow] = useState(t0);
  useEffect(() => {
    const t = setInterval(() => setNow(performance.now()), 100);
    return () => clearInterval(t);
  }, []);
  return (
    <div className="mx-auto w-full max-w-[1440px] px-5 pt-5 lg:px-8">
      <div className="flex items-center gap-3 text-[13px] text-muted">
        <LoaderCircle className="size-4 animate-spin text-accent" />
        {live ? `Reading ${sample ?? "the RFQ"} with the live model` : `Opening ${sample ?? "the draft"}`}
        <span className="num text-faint">{((now - t0) / 1000).toFixed(1)} s</span>
      </div>
      <div className="mt-4 shimmer h-[86px] rounded-2xl" />
      <div className="mt-5 grid gap-5 xl:grid-cols-[minmax(0,0.82fr)_minmax(0,1.5fr)]">
        <div className="shimmer h-[520px] rounded-2xl" />
        <div className="space-y-3">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="shimmer h-[64px] rounded-xl" />
          ))}
        </div>
      </div>
    </div>
  );
}
