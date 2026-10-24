import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { post, usePoll } from "../api.js";
import { Badge, Button, Card, Table, cx, inputCls, money, num } from "../ui.jsx";

const ACTOR_TONE = {
  matcher: "bg-slate-500", investigator: "bg-blue-600", "preparer-agent": "bg-indigo-600", "approver-agent": "bg-amber-600",
  "approval-policy": "bg-amber-500", executor: "bg-emerald-600", outreach: "bg-violet-600", learner: "bg-fuchsia-600",
  workflow: "bg-slate-400", "Sam Lee": "bg-rose-600", "Dana Ortiz": "bg-rose-500", verify: "bg-emerald-700",
};

function SourceCompare({ b }) {
  const vals = Object.entries(b.vals).filter(([k]) => !["unexplained", "bank_status", "return_code"].includes(k));
  const nums = vals.map(([, v]) => v).filter((v) => !isNaN(v));
  const differ = new Set(nums).size > 1;
  return (
    <div className="rounded-lg border border-slate-200 p-3">
      <div className="mb-2 flex items-center justify-between text-xs">
        <span className="font-mono text-slate-500">{b.break_id} · {b.detection_key}</span>
        <Badge status={b.status} />
      </div>
      <div className="grid gap-2" style={{ gridTemplateColumns: `repeat(${vals.length}, minmax(0, 1fr))` }}>
        {vals.map(([k, v]) => (
          <div key={k} className={cx("rounded-md px-3 py-2", differ && k === "ledger" ? "bg-rose-50 ring-1 ring-rose-200" : "bg-slate-50")}>
            <div className="text-[11px] font-medium uppercase tracking-wide text-slate-500">{k.replace("_", " ")}</div>
            <div className="num font-mono text-lg">{isNaN(v) ? String(v) : num(v)}</div>
          </div>
        ))}
      </div>
      {b.vals.bank_status && <div className="mt-2 text-xs text-slate-600">bank status: <b>{b.vals.bank_status}</b> {b.vals.return_code}</div>}
      <div className="mt-2 text-xs text-slate-600">{b.reason}</div>
    </div>
  );
}

function Postings({ postings }) {
  return (
    <table className="w-full text-xs">
      <tbody>
        {postings.map((p, i) => (
          <tr key={i} className="border-b border-slate-100 last:border-0">
            <td className="py-0.5 font-mono">{p.account}</td>
            <td className="py-0.5 text-slate-500">{p.asset}</td>
            <td className={cx("num py-0.5 text-right font-mono", Number(p.amount) < 0 ? "text-rose-700" : "text-emerald-700")}>
              {p.asset === "USD" ? money(p.amount) : num(p.amount)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function CaseDetail() {
  const { id } = useParams();
  const [d] = usePoll(`/cases/${id}`);
  const [q, setQ] = useState("Why was this case resolved this way?");
  const [ans, setAns] = useState(null);
  if (!d) return <div className="text-slate-400">Loading…</div>;
  const c = d.case;
  const comp = c.completion;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="font-mono text-xl font-semibold">{c.case_id}</h1>
        <Badge status={c.status} />
        <span className="text-sm text-slate-500">{c.break_key}</span>
        <span className="text-sm text-slate-500">opened {c.opened_on}{c.closed_on ? ` · closed ${c.closed_on}` : ""}</span>
        {c.status === "needs_human" && !c.escalated && <Link to={`/teach/${c.case_id}`}><Button variant="bad">Teach this case →</Button></Link>}
        {c.escalated && <Badge status="needs_human">escalated to controller</Badge>}
      </div>

      {comp && (
        <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-4 text-sm">
          <div className="font-semibold text-emerald-900">Completed</div>
          {comp.playbook ? (
            <div className="mt-1 text-emerald-900">
              Resolved by playbook <b className="font-mono">{comp.playbook}</b> v{comp.version}, branch <b>{comp.branch}</b> —
              taught by <b>{comp.provenance?.taught_by}</b> on <span className="font-mono">{comp.provenance?.from_case}</span>,
              approved by <b>{comp.provenance?.approved_by}</b>.
            </div>
          ) : (
            <div className="mt-1 text-emerald-900">Resolved by a fix taught by <b>{comp.taught_by}</b> ({comp.teach_session}).</div>
          )}
          <div className="mt-1 text-emerald-800">Proposals executed: {comp.proposals?.join(", ") || "none"} · Verify: {comp.verify}</div>
        </div>
      )}
      {c.summary && <Card title="Root cause"><p className="text-sm leading-relaxed">{c.summary}</p></Card>}

      <Card title={`Source values (${d.breaks.length} break${d.breaks.length > 1 ? "s" : ""})`}>
        <div className="grid gap-3 md:grid-cols-2">{d.breaks.map((b) => <SourceCompare key={b.break_id} b={b} />)}</div>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Agent timeline">
          <ol className="relative space-y-3 border-l border-slate-200 pl-4">
            {d.events.map((e) => (
              <li key={e.seq} className="relative">
                <span className={cx("absolute -left-[21px] top-1.5 h-2.5 w-2.5 rounded-full", ACTOR_TONE[e.actor] || "bg-slate-400")} />
                <div className="flex flex-wrap items-baseline gap-2 text-xs">
                  <span className="font-mono text-slate-400">#{e.seq} {e.sim_date}</span>
                  <span className="font-semibold">{e.actor}</span>
                  <span className="text-slate-500">{e.action.replaceAll("_", " ")}</span>
                </div>
                <div className="text-sm text-slate-700">{e.reason}</div>
                {e.evidence?.length > 0 && (
                  <div className="mt-0.5 flex flex-wrap gap-1">
                    {e.evidence.slice(0, 6).map((ev, i) => (
                      <span key={i} className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[10px] text-slate-600">
                        {Object.entries(ev).map(([k, v]) => `${k}: ${typeof v === "object" ? "…" : v}`).join(" ")}
                      </span>
                    ))}
                  </div>
                )}
              </li>
            ))}
          </ol>
        </Card>
        <div className="space-y-4">
          <Card title="Proposals">
            {d.proposals.length === 0 && <div className="text-sm text-slate-400">None.</div>}
            <div className="space-y-3">
              {d.proposals.map((p) => {
                const apps = d.approvals.filter((a) => a.proposal_id === p.proposal_id);
                return (
                  <div key={p.proposal_id} className="rounded-lg border border-slate-200 p-3">
                    <div className="flex flex-wrap items-center gap-2 text-sm">
                      <span className="font-mono font-semibold">{p.proposal_id}</span>
                      <span>{p.body.action_name}</span>
                      <Badge status={p.status} />
                      <span className="ml-auto num font-semibold">{money(p.amount_usd)}</span>
                    </div>
                    <div className="mt-1 text-xs text-slate-500">
                      calculator <b className="font-mono">{p.body.calculator}</b> · prepared by {p.prepared_by} · routed to {p.route}
                      {p.body.references?.length > 0 && <> · corrects <span className="font-mono">{p.body.references.join(", ")}</span></>}
                    </div>
                    <p className="mt-1 text-sm text-slate-700">{p.body.memo}</p>
                    <div className="mt-2"><Postings postings={p.body.postings} /></div>
                    {apps.map((a) => (
                      <div key={a.id} className="mt-2 rounded bg-slate-50 px-2 py-1 text-xs">
                        <b>{a.decision}</b> by {a.decided_by} on {a.decided_on}: {a.reason}
                        <div className="font-mono text-[10px] text-slate-400">sha256 {a.proposal_hash.slice(0, 24)}…</div>
                      </div>
                    ))}
                  </div>
                );
              })}
            </div>
          </Card>
          <Card title="Messages">
            {d.messages.length === 0 && <div className="text-sm text-slate-400">None.</div>}
            <div className="space-y-2">
              {d.messages.map((m) => (
                <div key={m.msg_id} className={cx("rounded-lg p-2.5 text-sm", m.kind === "reply" ? "ml-8 bg-violet-50" : "mr-8 bg-slate-50")}>
                  <div className="text-xs text-slate-500">
                    <b>{m.from_party}</b> → {m.to_name} · {m.kind.replace("_", " ")} · {m.sent_on}
                  </div>
                  <div>{m.body}</div>
                </div>
              ))}
            </div>
          </Card>
          <Card title="Ask why (auditor, event log only)">
            <div className="flex gap-2">
              <input className={inputCls} value={q} onChange={(e) => setQ(e.target.value)} />
              <Button onClick={async () => setAns(await post("/ask", { case_id: c.case_id, question: q }))}>Ask</Button>
            </div>
            {ans && (
              <div className="mt-2 whitespace-pre-wrap rounded bg-slate-50 p-2 text-sm">
                {ans.answer}
                <div className="mt-1 text-xs text-slate-500">cites events {ans.citations.join(", ")} · {ans.backend}</div>
              </div>
            )}
          </Card>
        </div>
      </div>
    </div>
  );
}
