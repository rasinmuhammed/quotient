"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Health } from "@/lib/types";

type State = { kind: "waking" } | { kind: "up"; health: Health } | { kind: "down" };

/** Pings the API on load: a free-tier server sleeps, and this starts waking it before the first quote. */
export function ServerStatus() {
  const [state, setState] = useState<State>({ kind: "waking" });

  useEffect(() => {
    let alive = true;
    let tries = 0;
    const ping = () =>
      api
        .health()
        .then((health) => alive && setState({ kind: "up", health }))
        .catch(() => {
          if (!alive) return;
          if (++tries < 8) setTimeout(ping, 4000);
          else setState({ kind: "down" });
        });
    ping();
    return () => {
      alive = false;
    };
  }, []);

  const [dot, label, title] =
    state.kind === "up"
      ? state.health.live
        ? ["bg-accent", state.health.model?.replace(/^openai\//, "") ?? "live", `Live model: ${state.health.model} (reasoning ${state.health.reasoning_effort}). Rules ${state.health.rules_version}, FX ${state.health.fx_snapshot}.`]
        : ["bg-attn", "Replay only", "No model key on the server: samples replay stored runs; uploads are disabled."]
      : state.kind === "waking"
        ? ["bg-faint animate-pulse", "Waking server", "The API sleeps when idle; the first request takes up to a minute."]
        : ["bg-bad", "Server offline", "The quoting API did not answer."];

  return (
    <span title={title} className="inline-flex items-center gap-2 rounded-full border border-border bg-surface px-2.5 py-1 text-[12px] text-muted shadow-card">
      <span className={`size-1.5 rounded-full ${dot}`} />
      <span className="num">{label}</span>
    </span>
  );
}
