import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, post, usePoll } from "../api.js";
import { Button, Card, Field, inputCls } from "../ui.jsx";

export default function Inject() {
  const [opts] = usePoll("/inject/options", 5000);
  const [type, setType] = useState("admin_fee");
  const [lease, setLease] = useState("");
  const [res, setRes] = useState(null);
  const [err, setErr] = useState(null);
  const [found, setFound] = useState(null);
  const leases = (opts?.leases || []).filter((l) =>
    (type !== "admin_fee" || l.has_fee_clause) && (type !== "gross_up" || !l.has_gross_up_clause));
  useEffect(() => {
    if (leases.length && !leases.find((l) => l.lease_id === lease)) setLease(leases[0].lease_id);
  }, [leases, lease]);
  const go = async () => {
    setErr(null); setFound(null);
    try { setRes(await post("/inject", { type, lease_id: lease })); } catch (e) { setErr(e.message); }
  };
  const check = async () => {
    const bks = await api("/breaks?include_harmless=true");
    setFound(bks.find((b) => b.ref === res.statement_id) || "not yet");
  };
  if (!opts) return <div className="text-slate-400">Loading…</div>;
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card title="Plant an overcharge in a statement still to arrive">
        <div className="space-y-3">
          <Field label="Overcharge">
            <select className={inputCls} value={type} onChange={(e) => setType(e.target.value)}>
              {Object.entries(opts.types).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
          </Field>
          <Field label="Store (statement not yet received)">
            {leases.length ? (
              <select className={inputCls} value={lease} onChange={(e) => setLease(e.target.value)}>
                {leases.map((l) => <option key={l.lease_id} value={l.lease_id}>{l.lease_id} · {l.store} · {l.landlord}</option>)}
              </select>
            ) : <div className="text-sm text-slate-500">Every store's statement has arrived. Reset the replay to inject again.</div>}
          </Field>
          <Button onClick={go} disabled={!leases.length}>Land it on {opts.lands_on}</Button>
          {err && <div className="rounded bg-rose-50 p-2 text-sm text-rose-800">{err}</div>}
        </div>
      </Card>
      <Card title="What happens">
        <p className="text-sm text-slate-600">
          The landlord's statement for that store arrives on the next replay day carrying the error, AP books it as billed,
          and an answer-key row records the credit the lease calls for. Press Play or Step: the matcher reads the statement
          against the lease, the county and the bank, the agents handle it, and the Proof page scores it.
        </p>
        {res && (
          <div className="mt-3 space-y-2 rounded bg-slate-50 p-3 text-sm">
            <div><b>{res.injection}</b> lands on <span className="font-mono">{res.effective_on}</span> as statement <span className="font-mono">{res.statement_id}</span>.</div>
            <div className="text-xs text-slate-500">Expected: {res.answer_key.expected_detection_keys.join(", ")} → {res.answer_key.expected_final_status}</div>
            <Button variant="ghost" onClick={check}>Where is it now?</Button>
            {found && (found === "not yet" ? <div className="text-xs">Not detected yet. Step the replay.</div> :
              <div className="text-sm">Detected as <span className="font-mono">{found.break_id}</span>, status <b>{found.status_label}</b>
                {found.case_id && <> · <Link className="text-blue-700" to={`/cases/${found.case_id}`}>{found.case_id}</Link></>}</div>)}
          </div>
        )}
      </Card>
    </div>
  );
}
