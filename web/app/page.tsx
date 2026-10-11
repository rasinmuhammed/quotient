import { Intake } from "@/components/intake";

const STAGES = [
  { name: "Read", who: "model", text: "Lines, quantities, units, grades and terms, each with the exact text it came from. Text not found in the document is discarded." },
  { name: "Match", who: "rules first", text: "CAS number, SKU, synonym table, then strict spelling. Only what is left goes to the model, which may only pick among five candidates." },
  { name: "Price", who: "engine", text: "Price-list tier, discount, margin floor, freight, insurance, FX, rounding: each step names its rule and source row." },
  { name: "Review", who: "you", text: "Anything uncertain is held for you with the reason. Approve, edit or reprice; the reply email is checked against the numbers." },
];

export default function Home() {
  return (
    <div className="mx-auto w-full max-w-[1180px] px-5 pt-12 pb-20 lg:px-8">
      <div className="rise max-w-[720px]">
        <h1 className="text-[34px] leading-[1.1] font-semibold tracking-[-0.025em] text-balance sm:text-[40px]">
          An RFQ in, a priced quote draft out.
        </h1>
        <p className="mt-3 max-w-[620px] text-[15px] leading-relaxed text-muted text-pretty">
          Drop an email, PDF or spreadsheet. Every price comes from the price list through a named rule, and the model
          never writes a number. What the system is unsure of is held for you, never guessed.
        </p>
      </div>

      <Intake />

      <section className="mt-14">
        <h2 className="text-[12px] font-medium tracking-[0.06em] text-faint uppercase">What happens to an RFQ</h2>
        <ol className="mt-4 grid gap-px overflow-hidden rounded-xl border border-border bg-border sm:grid-cols-2 lg:grid-cols-4">
          {STAGES.map((s, i) => (
            <li key={s.name} className="bg-surface p-5">
              <div className="flex items-baseline gap-2">
                <span className="num text-[12px] text-faint">{i + 1}</span>
                <span className="text-[14px] font-semibold">{s.name}</span>
                <span className="ml-auto rounded-full bg-surface-2 px-2 py-0.5 text-[11px] text-muted">{s.who}</span>
              </div>
              <p className="mt-2 text-[13px] leading-relaxed text-muted">{s.text}</p>
            </li>
          ))}
        </ol>
      </section>
    </div>
  );
}
