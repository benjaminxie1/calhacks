import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, NavLink, Route, Routes } from "react-router-dom";
import "./index.css";
import Header from "./Header.jsx";
import Overview from "./pages/Overview.jsx";
import Breaks from "./pages/Breaks.jsx";
import CaseDetail from "./pages/CaseDetail.jsx";
import Approvals from "./pages/Approvals.jsx";
import Teach from "./pages/Teach.jsx";
import Playbooks from "./pages/Playbooks.jsx";
import Proof from "./pages/Proof.jsx";
import Inject from "./pages/Inject.jsx";
import Audit from "./pages/Audit.jsx";
import { usePoll } from "./api.js";

function Nav() {
  const [st] = usePoll("/state");
  const items = [
    ["/", "Overview"], ["/breaks", "Breaks"],
    ["/approvals", "Approvals", st?.pending_approvals], ["/teach", "Teach mode", st?.needs_human],
    ["/playbooks", "Playbooks"], ["/proof", "Proof"], ["/inject", "Inject break"], ["/audit", "Audit"],
  ];
  return (
    <nav className="flex gap-1 overflow-x-auto border-b border-slate-200 bg-white px-4">
      {items.map(([to, label, n]) => (
        <NavLink key={to} to={to} end={to === "/"}
          className={({ isActive }) =>
            `flex items-center gap-1.5 whitespace-nowrap border-b-2 px-3 py-2.5 text-sm font-medium ${isActive ? "border-slate-900 text-slate-900" : "border-transparent text-slate-500 hover:text-slate-800"}`}>
          {label}
          {n > 0 && <span className="rounded-full bg-rose-600 px-1.5 text-[10px] font-semibold text-white">{n}</span>}
        </NavLink>
      ))}
    </nav>
  );
}

function App() {
  return (
    <BrowserRouter>
      <Header />
      <Nav />
      <main className="mx-auto max-w-7xl p-4">
        <Routes>
          <Route path="/" element={<Overview />} />
          <Route path="/breaks" element={<Breaks />} />
          <Route path="/cases/:id" element={<CaseDetail />} />
          <Route path="/approvals" element={<Approvals />} />
          <Route path="/teach" element={<Teach />} />
          <Route path="/teach/:id" element={<Teach />} />
          <Route path="/playbooks" element={<Playbooks />} />
          <Route path="/proof" element={<Proof />} />
          <Route path="/inject" element={<Inject />} />
          <Route path="/audit" element={<Audit />} />
        </Routes>
      </main>
    </BrowserRouter>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
