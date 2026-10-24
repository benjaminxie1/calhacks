import { post, usePoll } from "./api.js";
import { Button, cx } from "./ui.jsx";

export default function Header() {
  const [st, , reload] = usePoll("/state", 1000);
  const act = async (p, body) => { await post(p, body); reload(); };
  const done = st?.last_done;
  const days = st?.days_total || 81;
  const idx = st?.day_index || 0;
  return (
    <header className="bg-slate-900 text-slate-100">
      <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3">
        <div>
          <div className="text-lg font-semibold tracking-tight">Breakpoint</div>
          <div className="text-xs text-slate-400">Reconciliation team · Meridian Securities (mock)</div>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="dark" onClick={() => act(st?.running ? "/replay/pause" : "/replay/play")}>
            {st?.running ? "❚❚ Pause" : "▶ Play"}
          </Button>
          <Button variant="dark" onClick={() => act("/replay/step")} disabled={st?.running}>Step 1 day</Button>
          <select className="rounded-lg border border-slate-600 bg-slate-800 px-2 py-1.5 text-sm"
            value={st?.speed || "fast"} onChange={(e) => act("/mode", { speed: e.target.value })}>
            <option value="slow">slow</option><option value="normal">normal</option><option value="fast">fast</option>
          </select>
        </div>
        <div className="min-w-48">
          <div className="text-xs text-slate-400">Simulated date</div>
          <div className="font-mono text-sm">{done || "before Jul 1, 2026"}</div>
          <div className="mt-1 h-1 w-48 rounded bg-slate-700">
            <div className="h-1 rounded bg-emerald-400" style={{ width: `${Math.round(100 * (st?.days_done || 0) / days)}%` }} />
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-3 text-xs">
          <label className="flex items-center gap-1.5">
            <span className="text-slate-400">Controller</span>
            <select className="rounded border border-slate-600 bg-slate-800 px-1.5 py-1" value={st?.controller_mode || "human"}
              onChange={(e) => act("/mode", { controller: e.target.value })}>
              <option value="human">you (UI)</option><option value="scripted">scripted Sam Lee</option>
            </select>
          </label>
          <label className="flex items-center gap-1.5">
            <span className="text-slate-400">Ops lead</span>
            <select className="rounded border border-slate-600 bg-slate-800 px-1.5 py-1" value={st?.teach_mode || "human"}
              onChange={(e) => act("/mode", { teach: e.target.value })}>
              <option value="human">you (UI)</option><option value="scripted">scripted Dana Ortiz</option>
            </select>
          </label>
          <span className={cx("rounded-full px-2 py-0.5", st?.llm === "claude" ? "bg-emerald-800 text-emerald-100" : "bg-slate-700 text-slate-300")}>
            agents: {st?.llm === "claude" ? "Claude" : "offline policies"}
          </span>
          <Button variant="dark" onClick={() => confirm("Reset the replay to Jul 1?") && act("/replay/reset")}>Reset</Button>
        </div>
        {st?.error && <div className="w-full rounded bg-rose-900 px-2 py-1 text-xs">{st.error}</div>}
      </div>
    </header>
  );
}
