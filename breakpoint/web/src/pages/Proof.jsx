import { usePoll } from "../api.js";
import { Badge, Card, Stat, Table } from "../ui.jsx";

const REAL = [
  ["Lease math", "Real CAM clauses: pro-rata share on GLA, controllable cap, admin fee base, capital exclusion, gross-up"],
  ["Leases and statements", "Simulated by the seeded generator: 40 stores, 12 landlords (mock)"],
  ["County tax records", "Simulated per parcel (APN), standing in for the assessor's lookup"],
  ["Ledger engine", "Real double-entry ledger on Postgres; simulated entries"],
  ["AP payments and bank", "Simulated estimate payments with bank clearing status"],
  ["People", "Personas answering from the true world (LLM-played when a key is set)"],
  ["Overcharges", "Planted, with an answer key"],
];

export default function Proof() {
  const [r] = usePoll("/proof", 4000);
  if (!r) return <div className="text-slate-400">Loading…</div>;
  const det = r.detection;
  const inv = r.invariants.checks;
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        <Stat label="Detection recall" value={det.recall} sub="planted overcharges found" tone="text-emerald-700" />
        <Stat label="Detection precision" value={det.precision} sub={`${det.false_positives.length} unplanted findings`} tone="text-emerald-700" />
        <Stat label="Resolved correctly" value={`${r.resolution.correct}/${r.resolution.total}`} sub="status + answer-key amounts" />
        <Stat label="Ledger = true world" value={r.ledger_vs_truth.equal ? "yes" : "no"} tone={r.ledger_vs_truth.equal ? "text-emerald-700" : "text-rose-600"}
          sub="every lease and cash balance" />
        <Stat label="Invariants" value={r.invariants.all_green ? "all green" : "failing"} tone={r.invariants.all_green ? "text-emerald-700" : "text-rose-600"} />
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Per finding type vs the answer key">
          <Table rows={Object.entries(det.per_type).map(([t, v]) => ({ t, ...v }))} rowKey={(x) => x.t} cols={[
            { key: "t", label: "Type", mono: true },
            { key: "planted", label: "Planted", right: true },
            { key: "detected", label: "Detected", right: true },
            { key: "recall", label: "Recall", right: true },
            { key: "resolved_correctly", label: "Resolved correctly", right: true },
          ]} />
          
        </Card>
        <Card title="Ledger invariants">
          <ul className="space-y-1.5 text-sm">
            {Object.entries(inv).map(([k, v]) => (
              <li key={k} className="flex items-center justify-between">
                <span>{k.replaceAll("_", " ")}</span>
                <span className={v ? "text-emerald-600" : "text-rose-600"}>{v ? "● pass" : "● fail"}</span>
              </li>
            ))}
          </ul>
        </Card>
      </div>
      <Card title="Every planted error">
        <Table rows={r.resolution.rows} rowKey={(x) => x.break_id} cols={[
          { key: "break_id", label: "Answer key", mono: true },
          { key: "type", label: "Type", mono: true },
          { key: "detected", label: "Detected", render: (x) => (x.detected ? "yes" : "no") },
          { key: "first_status", label: "First", render: (x) => (x.first_status ? <Badge status={x.first_status} /> : "—") },
          { key: "final_status", label: "Final", render: (x) => <Badge status={x.final_status} /> },
          { key: "expected_status", label: "Expected", render: (x) => <Badge status={x.expected_status} /> },
          { key: "amounts_ok", label: "Amounts", render: (x) => <span className={x.amounts_ok ? "text-emerald-700" : "text-rose-600"}>{x.detail}</span> },
          { key: "note", label: "Note", render: (x) => <span className="text-xs text-slate-500">{x.note}</span> },
        ]} />
      </Card>
      <Card title="Real vs simulated">
        <Table rows={REAL.map(([a, b]) => ({ a, b }))} rowKey={(x) => x.a} cols={[{ key: "a", label: "Part" }, { key: "b", label: "Status" }]} />
      </Card>
    </div>
  );
}
