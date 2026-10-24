import { useEffect, useRef, useState, useCallback } from "react";

export async function api(path, opts = {}) {
  const res = await fetch(`/api${path}`, {
    headers: { "Content-Type": "application/json" },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch {}
    throw new Error(msg);
  }
  return res.json();
}

export const post = (path, body) => api(path, { method: "POST", body });

/** Poll an endpoint every `ms` (2 s by default). Returns [data, error, reload]. */
export function usePoll(path, ms = 2000) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const alive = useRef(true);
  const load = useCallback(async () => {
    if (!path) return;
    try {
      const d = await api(path);
      if (alive.current) { setData(d); setError(null); }
    } catch (e) {
      if (alive.current) setError(e.message);
    }
  }, [path]);
  useEffect(() => {
    alive.current = true;
    load();
    const t = setInterval(load, ms);
    return () => { alive.current = false; clearInterval(t); };
  }, [load, ms]);
  return [data, error, load];
}
