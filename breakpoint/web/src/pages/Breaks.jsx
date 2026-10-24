import { useMemo, useState } from "react";
import { usePoll } from "../api.js";
import { Badge, Card, CaseLink, Table, inputCls, money, num } from "../ui.jsx";

export default function Breaks() {
  const [harmless, setHarmless] = useState(false);
  const [rows] = usePoll(`/breaks?include_harmless=${harmless}`);
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [type, setType] = useState("");
  const types = useMemo(() => [...new Set((rows || []).map((r) => r.type))].sort(), [rows]);
  const shown = (rows || []).filter((r) =>
    (!status || r.status_label === status) && (!type || r.type === type) &&
    (!q || [r.break_id, r.account_id, r.symbol, r.ref, r.case_id, r.playbook_id].join(" ").toLowerCase().includes(q.toLowerCase())));
  return (
    <Card title={`Breaks (${shown.length})`}>
      <div className="mb-3 flex flex-wrap items-center gap-2 [&>input]:w-56 [&>select]:w-44">
        <input className={inputCls.replace("w-full", "")} placeholder="search id, account, symbol…" value={q} onChange={(e) => setQ(e.target.value)} />
        <select className={inputCls.replace("w-full", "")} value={type} onChange={(e) => setType(e.target.value)}>
          <option value="">all types</option>{types.map((t) => <option key={t}>{t}</option>)}
        </select>
        <select className={inputCls.replace("w-full", "")} value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">all statuses</option>
          {["new", "investigating", "waiting", "awaiting_approval", "needs_human", "completed", "auto_cleared", "open"].map((s) => <option key={s}>{s}</option>)}
        </select>
        <label className="flex items-center gap-1 text-xs text-slate-600">
          <input type="checkbox" checked={harmless} onChange={(e) => setHarmless(e.target.checked)} /> show harmless
        </label>
      </div>
      <Table rows={shown} rowKey={(r) => r.break_id} empty="No breaks yet. Press Play." cols={[
        { key: "break_id", label: "ID", mono: true },
        { key: "type", label: "Type" },
        { key: "account_id", label: "Account", mono: true, render: (r) => r.account_id || "—" },
        { key: "symbol", label: "Symbol", render: (r) => r.symbol || "—" },
        { key: "amount_usd", label: "Amount", right: true, render: (r) => money(r.amount_usd) },
        { key: "sources", label: "Sources", render: (r) => r.sources.join(" vs ") },
        { key: "vals", label: "Values", render: (r) => <span className="text-xs text-slate-600">{Object.entries(r.vals).filter(([k]) => k !== "unexplained").map(([k, v]) => `${k} ${typeof v === "string" && !isNaN(v) ? num(v) : v}`).join(" · ")}</span> },
        { key: "detected_on", label: "Detected", mono: true },
        { key: "age", label: "Age", right: true, render: (r) => `${r.age}d` },
        { key: "status_label", label: "Status", render: (r) => <Badge status={r.status_label} /> },
        { key: "agent", label: "Last actor", render: (r) => <span className="text-xs">{r.agent || "matcher"}</span> },
        { key: "playbook_id", label: "Playbook", render: (r) => r.playbook_id ? <span className="font-mono text-xs">{r.playbook_id}{r.branch_id ? ` · ${r.branch_id}` : ""}</span> : "—" },
        { key: "case_id", label: "Case", render: (r) => <CaseLink id={r.case_id} /> },
      ]} />
    </Card>
  );
}
