import { useState } from "react";
import { usePoll } from "../api.js";
import { Card, CaseLink, Table, inputCls } from "../ui.jsx";

export default function Audit() {
  const [q, setQ] = useState("");
  const [rows] = usePoll(`/events?q=${encodeURIComponent(q)}`, 3000);
  return (
    <Card title="Event log (append-only)" right={<input className={inputCls + " w-64"} placeholder="search actor, action, case, reason…" value={q} onChange={(e) => setQ(e.target.value)} />}>
      <Table rows={rows || []} rowKey={(r) => r.seq} cols={[
        { key: "seq", label: "#", mono: true }, { key: "sim_date", label: "Sim date", mono: true },
        { key: "actor", label: "Actor" }, { key: "action", label: "Action", render: (r) => r.action.replaceAll("_", " ") },
        { key: "case_id", label: "Case", render: (r) => <CaseLink id={r.case_id} /> },
        { key: "reason", label: "Reason", render: (r) => <span className="text-xs">{r.reason}</span> },
      ]} />
    </Card>
  );
}
