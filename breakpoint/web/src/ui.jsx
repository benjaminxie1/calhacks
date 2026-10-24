import { Link } from "react-router-dom";

export const cx = (...c) => c.filter(Boolean).join(" ");

export function money(v, dp = 2) {
  if (v === null || v === undefined || v === "") return "—";
  const n = Number(v);
  return n.toLocaleString("en-US", { style: "currency", currency: "USD", minimumFractionDigits: dp, maximumFractionDigits: dp });
}
export const num = (v) => (v === null || v === undefined ? "—" : Number(v).toLocaleString("en-US", { maximumFractionDigits: 6 }));

export function Card({ title, right, children, className }) {
  return (
    <section className={cx("rounded-xl border border-slate-200 bg-white shadow-sm", className)}>
      {(title || right) && (
        <header className="flex items-center justify-between border-b border-slate-100 px-4 py-2.5">
          <h2 className="text-sm font-semibold text-slate-700">{title}</h2>
          {right}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

export function Stat({ label, value, sub, tone }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-sm">
      <div className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</div>
      <div className={cx("num mt-1 text-2xl font-semibold", tone)}>{value}</div>
      {sub && <div className="mt-0.5 text-xs text-slate-500">{sub}</div>}
    </div>
  );
}

const STATUS = {
  new: "bg-slate-100 text-slate-700",
  investigating: "bg-blue-100 text-blue-800",
  waiting: "bg-violet-100 text-violet-800",
  awaiting_approval: "bg-amber-100 text-amber-800",
  completed: "bg-emerald-100 text-emerald-800",
  auto_cleared: "bg-teal-100 text-teal-800",
  needs_human: "bg-rose-100 text-rose-800",
  open: "bg-slate-100 text-slate-700",
  resolved: "bg-emerald-100 text-emerald-800",
  pending: "bg-amber-100 text-amber-800",
  approved: "bg-blue-100 text-blue-800",
  executed: "bg-emerald-100 text-emerald-800",
  rejected: "bg-rose-100 text-rose-800",
  draft: "bg-amber-100 text-amber-800",
  active: "bg-emerald-100 text-emerald-800",
  flagged: "bg-rose-100 text-rose-800",
  retired: "bg-slate-100 text-slate-500",
};
const LABEL = { awaiting_approval: "awaiting approval", needs_human: "needs human", auto_cleared: "auto-cleared", waiting: "waiting on someone" };

export function Badge({ status, children }) {
  return (
    <span className={cx("inline-flex items-center whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium", STATUS[status] || "bg-slate-100 text-slate-700")}>
      {children || LABEL[status] || status}
    </span>
  );
}

export function Button({ children, onClick, variant = "primary", disabled, className, type = "button" }) {
  const v = {
    primary: "bg-slate-900 text-white hover:bg-slate-700",
    good: "bg-emerald-600 text-white hover:bg-emerald-500",
    bad: "bg-rose-600 text-white hover:bg-rose-500",
    ghost: "border border-slate-300 bg-white text-slate-700 hover:bg-slate-50",
    dark: "border border-slate-600 bg-slate-800 text-slate-100 hover:bg-slate-700",
  }[variant];
  return (
    <button type={type} onClick={onClick} disabled={disabled}
      className={cx("rounded-lg px-3 py-1.5 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-40", v, className)}>
      {children}
    </button>
  );
}

export function Table({ cols, rows, empty = "Nothing yet.", rowKey }) {
  if (!rows || rows.length === 0) return <div className="py-6 text-center text-sm text-slate-400">{empty}</div>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-500">
            {cols.map((c) => <th key={c.key} className={cx("px-2 py-2 font-medium", c.right && "text-right")}>{c.label}</th>)}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={rowKey ? rowKey(r) : i} className="border-b border-slate-100 last:border-0 hover:bg-slate-50">
              {cols.map((c) => (
                <td key={c.key} className={cx("px-2 py-1.5 align-top", c.right && "num text-right", c.mono && "font-mono text-xs")}>
                  {c.render ? c.render(r) : r[c.key]}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export const CaseLink = ({ id }) => id ? <Link className="font-mono text-xs text-blue-700 hover:underline" to={`/cases/${id}`}>{id}</Link> : <span className="text-slate-400">—</span>;

export function Field({ label, children }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium uppercase tracking-wide text-slate-500">{label}</span>
      {children}
    </label>
  );
}
export const inputCls = "w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-sm focus:border-slate-500 focus:outline-none";
