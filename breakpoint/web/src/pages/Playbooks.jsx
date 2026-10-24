import { post, usePoll } from "../api.js";
import { Badge, Button, Card } from "../ui.jsx";

export default function Playbooks() {
  const [rows, , reload] = usePoll("/playbooks");
  if (!rows) return <div className="text-slate-400">Loading…</div>;
  const byId = {};
  rows.forEach((r) => (byId[r.id] = byId[r.id] || []).push(r));
  const act = async (r, a) => { await post(`/playbooks/${r.id}/${r.version}/${a}`); reload(); };
  return (
    <div className="space-y-4">
      <p className="text-sm text-slate-600">
        One playbook per break key. Choosing it is a lookup; branches are checked in order and exactly one may match.
        Learning adds branches a controller approves — never model weights — so every behavior is readable and revocable.
      </p>
      {Object.entries(byId).map(([id, versions]) => {
        const cur = versions[0];
        return (
          <Card key={id} title={<span className="font-mono">{id}</span>} right={
            <div className="flex items-center gap-2">
              <span className="font-mono text-xs text-slate-500">{cur.key_name}</span>
              <Badge status={cur.status} />
              {cur.status === "draft" && <Button variant="good" onClick={() => act(cur, "approve")}>Approve</Button>}
              {cur.status === "flagged" && <Button variant="good" onClick={() => act(cur, "approve")}>Re-approve</Button>}
              {cur.status === "active" && <Button variant="ghost" onClick={() => act(cur, "flag")}>Flag</Button>}
              {cur.status !== "retired" && <Button variant="ghost" onClick={() => act(cur, "retire")}>Retire</Button>}
            </div>}>
            <div className="mb-2 text-xs text-slate-500">
              v{cur.version} · context: <span className="font-mono">{cur.body.context.join(", ")}</span> · approval: agent ≤ ${cur.body.approval.auto_under_usd}, otherwise {cur.body.approval.otherwise} · verify: {cur.body.verify}
            </div>
            <div className="space-y-2">
              {cur.body.branches.map((b, i) => {
                const st = cur.body.stats?.[b.id] || { used: 0, succeeded: 0 };
                return (
                  <div key={b.id} className="grid gap-2 rounded-lg border border-slate-200 p-3 md:grid-cols-4">
                    <div className="md:col-span-3">
                      <div className="text-sm"><span className="text-slate-400">{i + 1}.</span> <b>{b.id}</b></div>
                      <div className="font-mono text-xs">when <span className="text-blue-800">{b.when}</span></div>
                      <div className="font-mono text-xs">trust <b>{b.trust}</b> → {b.actions.join(", ")}</div>
                      <div className="mt-1 text-xs text-slate-500">
                        taught by <b>{b.provenance.taught_by}</b> from <span className="font-mono">{b.provenance.from_case}</span>
                        {b.provenance.approved_by && <>, approved by <b>{b.provenance.approved_by}</b></>}
                      </div>
                    </div>
                    <div className="text-right">
                      <div className="num text-2xl font-semibold">{st.succeeded}/{st.used}</div>
                      <div className="text-xs text-slate-500">succeeded / used</div>
                    </div>
                  </div>
                );
              })}
              <div className="rounded-lg border border-dashed border-slate-300 p-2 text-xs text-slate-500">else → needs human</div>
              {cur.body.modifiers?.map((m, i) => (
                <div key={i} className="font-mono text-xs text-slate-500">modifier: when {m.when} skip {m.skip.join(", ")}</div>
              ))}
            </div>
            <details className="mt-3 text-xs text-slate-500">
              <summary className="cursor-pointer">Version history ({versions.length})</summary>
              <ul className="mt-1 space-y-1">
                {versions.map((v) => (
                  <li key={v.version}>v{v.version} <Badge status={v.status} /> created {v.created_on} by {v.created_by}
                    {v.approved_by && `, approved by ${v.approved_by}`} — {v.note}</li>
                ))}
              </ul>
            </details>
          </Card>
        );
      })}
    </div>
  );
}
