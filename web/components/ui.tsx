import { CircleCheck, FileSpreadsheet, FileText, Mail, TriangleAlert, Info } from "lucide-react";
import type { ReactNode } from "react";
import { FLAG_LABEL } from "@/lib/format";
import type { Flag } from "@/lib/types";

export function FormatIcon({ format }: { format: string }) {
  const Icon = format === "xlsx" ? FileSpreadsheet : format === "eml" ? Mail : FileText;
  const tone =
    format === "xlsx" ? "text-[#1d7a45]" : format === "pdf" ? "text-[#b4361f]" : format === "eml" ? "text-accent" : "text-muted";
  return (
    <span className={`grid size-8 shrink-0 place-items-center rounded-lg bg-surface-2 ${tone}`}>
      <Icon className="size-4" />
    </span>
  );
}

export function FlagChip({ flag, approved = false, compact = false }: { flag: Flag; approved?: boolean; compact?: boolean }) {
  const block = flag.severity === "block" && !approved;
  const cls = block
    ? "border-attn-line bg-attn-soft text-attn"
    : "border-border bg-surface-2 text-muted";
  return (
    <span
      title={`${flag.message}${flag.rule_id ? ` (${flag.rule_id})` : ""}`}
      className={`inline-flex items-center gap-1 rounded-md border px-1.5 py-px text-[11.5px] font-medium whitespace-nowrap ${cls}`}
    >
      {block ? <TriangleAlert className="size-3" /> : flag.severity === "block" ? <CircleCheck className="size-3" /> : <Info className="size-3" />}
      {!compact && FLAG_LABEL[flag.code]}
    </span>
  );
}

export function RuleTag({ id, title }: { id: string; title?: string }) {
  return (
    <span title={title} className="num rounded bg-surface-2 px-1.5 py-px font-mono text-[10.5px] text-muted">
      {id}
    </span>
  );
}

export function Label({ children }: { children: ReactNode }) {
  return <div className="text-[11px] font-medium tracking-[0.05em] text-faint uppercase">{children}</div>;
}

export function Button({
  children,
  onClick,
  kind = "secondary",
  disabled,
  title,
  type = "button",
}: {
  children: ReactNode;
  onClick?: () => void;
  kind?: "primary" | "secondary" | "ghost" | "accent";
  disabled?: boolean;
  title?: string;
  type?: "button" | "submit";
}) {
  const styles = {
    primary: "bg-text text-bg hover:opacity-90",
    accent: "bg-accent text-on-accent hover:bg-accent-strong",
    secondary: "border border-border bg-surface text-text shadow-card hover:bg-surface-2",
    ghost: "text-muted hover:bg-surface-2 hover:text-text",
  }[kind];
  return (
    <button
      type={type}
      title={title}
      disabled={disabled}
      onClick={onClick}
      className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-[13px] font-medium whitespace-nowrap transition-colors disabled:pointer-events-none disabled:opacity-40 ${styles}`}
    >
      {children}
    </button>
  );
}
