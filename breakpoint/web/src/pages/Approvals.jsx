import { useState } from "react";
import { post, usePoll } from "../api.js";
import { Badge, Button, Card, CaseLink, cx, money, num } from "../ui.jsx";

export default function Approvals() {
  const [d, , reload] = usePoll("/approvals");
  const [busy, setBusy] = useState(null);
  const [err, setErr] = useState(null);
  const act = async (fn, id) => {
    setBusy(id); setErr(null);
    try { await fn(); } catch (e) { setErr(e.message); }
    setBusy(null); reload();
  };
  if (!d) return <div className="text-slate-400">Loading…</div>;
  return (
    <div className="space-y-4">
      <p className="text-sm text-slate-600">
        Controller queue (Sam Lee). The approver agent handles proposals of $500 or less from active playbooks; everything
        else — larger entries, client money movements, taught one-off fixes and every new playbook — lands here.
      </p>
      {err && <div className="rounded bg-rose-50 p-2 text-sm text-rose-800">{err}</div>}
      <Card title={`Proposals awaiting the controller (${d.proposals.length})`}>
        {d.proposals.length === 0 && <div className="text-sm text-slate-400">Queue is empty.</div>}
        <div className="space-y-3">
          {d.proposals.map((p) => (
            <div key={p.proposal_id} className="grid gap-3 rounded-lg border border-slate-200 p-3 lg:grid-cols-2">
              <div>
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-mono font-semibold">{p.proposal_id}</span>
                  <span className="text-sm">{p.body.action_name}</span>
                  <CaseLink id={p.case_id} />
                  <span className="ml-auto text-lg font-semibold num">{money(p.amount_usd)}</span>
                </div>
                <p className="mt-1 text-sm text-slate-700">{p.body.memo}</p>
                <div className="mt-1 text-xs text-slate-500">
                  calculator <b className="font-mono">{p.body.calculator}</b> ·
                  {p.body.playbook ? ` playbook ${p.body.playbook.id} v${p.body.playbook.version} · ${p.body.playbook.branch}` : " taught fix (no playbook yet)"}
                </div>
                <table className="mt-2 w-full text-xs">
                  <tbody>
                    {p.body.postings.map((x, i) => (
                      <tr key={i}><td className="font-mono">{x.account}</td><td>{x.asset}</td>
                        <td className={cx("text-right font-mono num", Number(x.amount) < 0 ? "text-rose-700" : "text-emerald-700")}>{x.asset === "USD" ? money(x.amount) : num(x.amount)}</td></tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="flex flex-col justify-between gap-2">
                <div className="space-y-2">
                  <div className="text-xs font-medium uppercase tracking-wide text-slate-500">Evidence</div>
                  {p.case?.summary && <p className="text-sm">{p.case.summary}</p>}
                  {p.breaks.map((b) => (
                    <div key={b.break_id} className="rounded bg-slate-50 px-2 py-1 text-xs">
                      <span className="font-mono">{b.detection_key}</span> — {b.reason}
                    </div>
                  ))}
                </div>
                <div className="flex gap-2">
                  <Button variant="good" disabled={busy} onClick={() => act(() => post(`/approvals/${p.proposal_id}`, { decision: "approved", reason: "evidence reviewed in the UI" }), p.proposal_id)}>Approve</Button>
                  <Button variant="bad" disabled={busy} onClick={() => act(() => post(`/approvals/${p.proposal_id}`, { decision: "rejected", reason: "rejected in the UI" }), p.proposal_id)}>Reject</Button>
                </div>
              </div>
            </div>
          ))}
        </div>
      </Card>
      <Card title={`Draft playbooks awaiting approval (${d.playbooks.length})`}>
        {d.playbooks.length === 0 && <div className="text-sm text-slate-400">No drafts.</div>}
        {d.playbooks.map((pb) => (
          <div key={`${pb.id}-${pb.version}`} className="rounded-lg border border-slate-200 p-3">
            <div className="flex items-center gap-2">
              <span className="font-mono font-semibold">{pb.id}</span> v{pb.version} <Badge status={pb.status} />
              <span className="text-xs text-slate-500">{pb.note}</span>
            </div>
            {pb.body.branches.map((b) => (
              <div key={b.id} className="mt-2 rounded bg-slate-50 p-2 font-mono text-xs">
                <div><b>{b.id}</b>: when <span className="text-blue-800">{b.when}</span></div>
                <div>trust {b.trust} → {b.actions.join(", ")}</div>
                <div className="text-slate-500">taught by {b.provenance.taught_by} on {b.provenance.from_case}{b.provenance.approved_by ? `, approved by ${b.provenance.approved_by}` : ""}</div>
              </div>
            ))}
            <div className="mt-2 flex gap-2">
              <Button variant="good" disabled={busy} onClick={() => act(() => post(`/playbooks/${pb.id}/${pb.version}/approve`), pb.id)}>Approve playbook</Button>
              <Button variant="ghost" disabled={busy} onClick={() => act(() => post(`/playbooks/${pb.id}/${pb.version}/retire`), pb.id)}>Reject</Button>
            </div>
          </div>
        ))}
      </Card>
    </div>
  );
}
