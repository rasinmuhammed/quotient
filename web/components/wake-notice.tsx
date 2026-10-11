"use client";

import { LoaderCircle } from "lucide-react";
import { useEffect, useState } from "react";

/** Shown while an API request is slow: the free-tier server sleeps when idle and takes ~30 s to wake. */
export function WakeNotice() {
  const [slow, setSlow] = useState(false);
  useEffect(() => {
    const on = (e: Event) => setSlow((e as CustomEvent<boolean>).detail);
    window.addEventListener("quotient:slow", on);
    return () => window.removeEventListener("quotient:slow", on);
  }, []);
  if (!slow) return null;
  return (
    <div role="status" className="rise fixed inset-x-0 bottom-5 z-50 flex justify-center px-4">
      <div className="flex items-center gap-2.5 rounded-full border border-border bg-surface px-4 py-2 text-[13px] text-muted shadow-pop">
        <LoaderCircle className="size-4 animate-spin text-accent" />
        Waking the server, ~30 s. The free tier sleeps when idle.
      </div>
    </div>
  );
}
