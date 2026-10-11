// Copies the latest eval results (../evals/results) into data/evals.json for the static Evals page.
// When the folder is not there (a deploy that only sees web/), the committed copy is used as is.
import { existsSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const src = join(here, "..", "..", "evals", "results");
const out = join(here, "..", "data", "evals.json");
const KEEP = 6;

if (!existsSync(src)) {
  console.log(`sync-evals: ${src} not found; using the committed data/evals.json`);
  process.exit(0);
}

const files = readdirSync(src).filter((f) => f.endsWith(".json")).sort().reverse();
const live = files.filter((f) => !f.endsWith("-oracle.json")).slice(0, KEEP - 1);
const oracle = files.find((f) => f.endsWith("-oracle.json"));
const runs = [...live, ...(oracle ? [oracle] : [])].map((f) => {
  const r = JSON.parse(readFileSync(join(src, f), "utf8"));
  return { name: f.replace(/\.json$/, ""), ...r };
});
writeFileSync(out, JSON.stringify({ synced: new Date().toISOString().slice(0, 10), runs }) + "\n");
console.log(`sync-evals: ${runs.length} runs -> data/evals.json`);
