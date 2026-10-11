import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { EvalReport } from "@/components/evals/report";
import { RUNS, runLabel } from "@/lib/evals";

export function generateStaticParams() {
  return RUNS.map((r) => ({ run: r.name }));
}

export async function generateMetadata({ params }: PageProps<"/evals/[run]">): Promise<Metadata> {
  const { run } = await params;
  const r = RUNS.find((x) => x.name === run);
  return { title: r ? `Evals · ${runLabel(r)}` : "Evals" };
}

export default async function EvalRunPage({ params }: PageProps<"/evals/[run]">) {
  const { run } = await params;
  const r = RUNS.find((x) => x.name === run);
  if (!r) notFound();
  return <EvalReport run={r} />;
}
