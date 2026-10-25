import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, post, usePoll } from "../api.js";
import { Badge, Button, Card, Field, Table, cx, inputCls, num } from "../ui.jsx";

export default function Teach() {
  const { id } = useParams();
  const [bks] = usePoll("/breaks");
  const nav = useNavigate();
  const needing = [...new Map((bks || []).filter((b) => b.case_status === "needs_human").map((b) => [b.case_id, b])).values()];
  if (!id) {
    return (
      <Card title="Cases that need a human">
        <p className="mb-3 text-sm text-slate-600">
          No playbook fits these. Walk one: pick the source to trust, choose actions from the library, and name the fact that
          makes this case different. The fix runs once through the controller; the learner drafts a playbook branch that the
          controller approves.
        </p>
        <Table rows={needing} rowKey={(r) => r.case_id} empty="Nothing needs a human right now." cols={[
          { key: "case_id", label: "Case", render: (r) => <Link className="font-mono text-xs text-blue-700" to={`/teach/${r.case_id}`}>{r.case_id}</Link> },
          { key: "type", label: "Type" }, { key: "store", label: "Store" }, { key: "landlord", label: "Landlord" },
          { key: "reason", label: "What the matcher saw" }, { key: "detected_on", label: "Detected", mono: true },
          { key: "go", label: "", render: (r) => <Button onClick={() => nav(`/teach/${r.case_id}`)}>Teach</Button> },
        ]} />
      </Card>
    );
  }
  return <TeachCase id={id} />;
}

function TeachCase({ id }) {
  const [d, setD] = useState(null);
  const [trusted, setTrusted] = useState("");
  const [actions, setActions] = useState([]);
  const [fact, setFact] = useState("");
  const [note, setNote] = useState("");
  const [res, setRes] = useState(null);
  const [err, setErr] = useState(null);
  useEffect(() => { api(`/teach/${id}`).then(setD).catch((e) => setErr(e.message)); }, [id]);
  if (!d) return <div className="text-slate-400">{err || "Loading…"}</div>;
  const c = d.case;
  const facts = Object.values(c.facts || {})[0] || {};
  const toggle = (a) => setActions((xs) => (xs.includes(a) ? xs.filter((x) => x !== a) : [...xs, a]));
  const submit = async () => {
    setErr(null);
    try { setRes(await post(`/teach/${id}`, { trusted_source: trusted, actions, fact, note })); } catch (e) { setErr(e.message); }
  };
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <h1 className="font-mono text-xl font-semibold">Teach {c.case_id}</h1><Badge status={c.status} />
        <Link to={`/cases/${c.case_id}`} className="text-sm text-blue-700">case detail →</Link>
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="What Breakpoint sees">
          <div className="mb-2 text-sm text-slate-600">{c.notes?.[c.notes.length - 1]?.text}</div>
          {d.breaks.map((b) => (
            <div key={b.break_id} className="mb-2 rounded bg-slate-50 p-2 text-sm">
              <span className="font-mono text-xs">{b.detection_key}</span>
              <div>{b.reason}</div>
            </div>
          ))}
          <div className="mt-3 text-xs font-medium uppercase tracking-wide text-slate-500">Facts from the context step</div>
          <table className="mt-1 w-full text-sm">
            <tbody>
              {Object.entries(facts).map(([k, v]) => (
                <tr key={k} className={cx("border-b border-slate-100", fact === k && "bg-amber-50")}>
                  <td className="py-1 font-mono text-xs">{k}</td>
                  <td className="py-1 text-right font-mono">{v === null ? "—" : typeof v === "boolean" ? String(v) : num(v)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
        <Card title="Your lesson">
          <div className="space-y-3">
            <Field label="1. Which source is right?">
              <div className="flex flex-wrap gap-2">
                {d.sources.map((s) => (
                  <button key={s} onClick={() => setTrusted(s)}
                    className={cx("rounded-full border px-3 py-1 text-sm", trusted === s ? "border-slate-900 bg-slate-900 text-white" : "border-slate-300")}>{s}</button>
                ))}
              </div>
            </Field>
            <Field label="2. Actions (from the closed library, in order)">
              <div className="flex flex-wrap gap-2">
                {Object.entries(d.actions).map(([k, a]) => (
                  <button key={k} onClick={() => toggle(k)} title={`${a.type}${a.template ? ` · ${a.template}` : ""}${a.to_role ? ` → ${a.to_role}` : ""}`}
                    className={cx("rounded-full border px-3 py-1 text-xs", actions.includes(k) ? "border-blue-700 bg-blue-700 text-white" : "border-slate-300")}>
                    {actions.includes(k) && <b>{actions.indexOf(k) + 1}. </b>}{k}
                  </button>
                ))}
              </div>
            </Field>
            <Field label="3. The fact that makes this case different">
              <select className={inputCls} value={fact} onChange={(e) => setFact(e.target.value)}>
                <option value="">choose a fact…</option>
                {d.facts_available.map((f) => <option key={f} value={f}>{f} = {String(facts[f])}</option>)}
              </select>
            </Field>
            <Field label="4. Note for the record">
              <textarea className={inputCls} rows={3} value={note} onChange={(e) => setNote(e.target.value)}
                placeholder="e.g. The lease has no gross-up clause, so the gross-up line comes off in full." />
            </Field>
            <Button disabled={!trusted || !actions.length || !fact || !note || res} onClick={submit}>Teach and run the fix once</Button>
            {err && <div className="rounded bg-rose-50 p-2 text-sm text-rose-800">{err}</div>}
            {res && (
              <div className="rounded bg-emerald-50 p-3 text-sm text-emerald-900">
                Recorded {res.teach_session}. The learner drafted <b className="font-mono">{res.draft.id}</b> v{res.draft.version}.
                The fix and the draft are in the <Link className="underline" to="/approvals">controller queue</Link>.
              </div>
            )}
          </div>
        </Card>
      </div>
    </div>
  );
}
