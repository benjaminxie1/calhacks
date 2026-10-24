import { useState } from "react";
import { Link } from "react-router-dom";
import { api, post, usePoll } from "../api.js";
import { Button, Card, Field, inputCls } from "../ui.jsx";

export default function Inject() {
  const [opts] = usePoll("/inject/options", 60000);
  const [type, setType] = useState("B2");
  const [acct, setAcct] = useState("ACC-0007");
  const [sym, setSym] = useState("KO");
  const [res, setRes] = useState(null);
  const [err, setErr] = useState(null);
  const [found, setFound] = useState(null);
  const go = async () => {
    setErr(null); setFound(null);
    try { setRes(await post("/inject", { type, account_id: acct, symbol: sym })); } catch (e) { setErr(e.message); }
  };
  const check = async () => {
    const bks = await api("/breaks?include_harmless=true");
    setFound(bks.find((b) => b.ref === res.order_id) || "not yet");
  };
  if (!opts) return <div className="text-slate-400">Loading…</div>;
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card title="Inject a break into the live replay">
        <div className="space-y-3">
          <Field label="Break type">
            <select className={inputCls} value={type} onChange={(e) => setType(e.target.value)}>
              {Object.entries(opts.types).map(([k, v]) => <option key={k} value={k}>{k} — {v}</option>)}
            </select>
          </Field>
          <Field label="Account">
            <select className={inputCls} value={acct} onChange={(e) => setAcct(e.target.value)}>
              {opts.accounts.map((a) => <option key={a.account_id} value={a.account_id}>{a.account_id} · {a.name} · {a.tier}</option>)}
            </select>
          </Field>
          <Field label="Symbol">
            <select className={inputCls} value={sym} onChange={(e) => setSym(e.target.value)}>
              {opts.symbols.map((s) => <option key={s}>{s}</option>)}
            </select>
          </Field>
          <Button onClick={go}>Inject into the next replay day</Button>
          {err && <div className="rounded bg-rose-50 p-2 text-sm text-rose-800">{err}</div>}
        </div>
      </Card>
      <Card title="What happens">
        <p className="text-sm text-slate-600">
          The injector creates a real trade for the account in every source system, then plants the break in the view that
          should disagree, and appends an answer-key row. Press Play or Step: the matcher finds it, the agents handle it, and
          the Proof page scores it.
        </p>
        {res && (
          <div className="mt-3 space-y-2 rounded bg-slate-50 p-3 text-sm">
            <div><b>{res.injection}</b> lands on <span className="font-mono">{res.effective_on}</span> as order <span className="font-mono">{res.order_id}</span>.</div>
            <div className="text-xs text-slate-500">Expected: {res.answer_key.expected_detection_keys.join(", ")} → {res.answer_key.expected_final_status}</div>
            <Button variant="ghost" onClick={check}>Where is it now?</Button>
            {found && (found === "not yet" ? <div className="text-xs">Not detected yet — step the replay.</div> :
              <div className="text-sm">Detected as <span className="font-mono">{found.break_id}</span>, status <b>{found.status_label}</b>
                {found.case_id && <> · <Link className="text-blue-700" to={`/cases/${found.case_id}`}>{found.case_id}</Link></>}</div>)}
          </div>
        )}
      </Card>
    </div>
  );
}
