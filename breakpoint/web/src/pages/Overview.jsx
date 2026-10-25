import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis, Legend } from "recharts";
import { Link } from "react-router-dom";
import { usePoll } from "../api.js";
import { Badge, Card, CaseLink, Stat, Table, money } from "../ui.jsx";

const ORDER = ["new", "investigating", "waiting", "awaiting_approval", "needs_human", "completed"];

export default function Overview() {
  const [o] = usePoll("/overview");
  const [bks] = usePoll("/breaks");
  if (!o) return <div className="text-slate-400">Loading…</div>;
  const recent = (bks || []).filter((b) => b.case_id).slice(0, 8);
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        <Stat label="Open cases" value={o.open_cases} sub={`${o.by_status.needs_human || 0} need a human`}
          tone={o.by_status.needs_human ? "text-rose-600" : ""} />
        <Stat label="Overcharges found" value={money(o.overcharge_found, 0)} sub={`${money(o.credits_posted, 0)} credited · ${money(o.dollars_at_risk, 0)} in open cases`} />
        <Stat label="Auto-resolution" value={o.auto_resolution_rate === null ? "—" : `${o.auto_resolution_rate}%`}
          sub={`${o.auto_resolved} of ${o.total_cases} cases, no human`} tone="text-emerald-700" />
        <Stat label="Median time to resolve" value={o.median_days_to_resolve === null ? "—" : `${o.median_days_to_resolve} bd`}
          sub="business days, completed cases" />
        <Stat label="Harmless auto-cleared" value={o.harmless_auto_cleared} sub="printed totals within $1 (rounding)" />
      </div>
      <div className="grid gap-4 lg:grid-cols-3">
        <Card title="Findings per day" className="lg:col-span-2">
          <div className="h-64">
            <ResponsiveContainer>
              <BarChart data={o.series} margin={{ left: -20 }}>
                <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#e2e8f0" />
                <XAxis dataKey="date" tick={{ fontSize: 10 }} tickFormatter={(d) => d.slice(5)} minTickGap={20} />
                <YAxis allowDecimals={false} tick={{ fontSize: 10 }} />
                <Tooltip />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                <Bar dataKey="breaks" stackId="a" fill="#e11d48" name="findings" />
                <Bar dataKey="harmless" stackId="a" fill="#94a3b8" name="harmless (auto-cleared)" />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Card>
        <Card title="Open cases by age">
          <div className="h-64">
            <ResponsiveContainer>
              <BarChart data={o.aging} margin={{ left: -20 }}>
                <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#e2e8f0" />
                <XAxis dataKey="bucket" tick={{ fontSize: 11 }} />
                <YAxis allowDecimals={false} tick={{ fontSize: 10 }} />
                <Tooltip />
                <Bar dataKey="cases" fill="#0f172a" />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Card>
      </div>
      <div className="grid gap-4 lg:grid-cols-3">
        <Card title="Cases by status">
          <div className="space-y-2">
            {ORDER.map((s) => (
              <div key={s} className="flex items-center justify-between">
                <Badge status={s} />
                <span className="num font-semibold">{o.by_status[s] || 0}</span>
              </div>
            ))}
          </div>
        </Card>
        <Card title="Latest findings" className="lg:col-span-2" right={<Link to="/breaks" className="text-xs text-blue-700">all findings →</Link>}>
          <Table rows={recent} rowKey={(r) => r.break_id} cols={[
            { key: "detected_on", label: "Detected", mono: true },
            { key: "type", label: "Type" },
            { key: "store", label: "Store" },
            { key: "amount_usd", label: "Amount", right: true, render: (r) => money(r.amount_usd) },
            { key: "case_id", label: "Case", render: (r) => <CaseLink id={r.case_id} /> },
            { key: "status_label", label: "Status", render: (r) => <Badge status={r.status_label} /> },
          ]} />
        </Card>
      </div>
    </div>
  );
}
