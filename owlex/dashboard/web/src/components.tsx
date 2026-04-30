import React from "react";

export function Card({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return <div className={`rounded-lg border border-zinc-800 bg-zinc-900/40 ${className}`}>{children}</div>;
}

export function StatusBadge({ status }: { status: string }) {
  const cls =
    status === "completed"
      ? "bg-emerald-500/15 text-emerald-300 border-emerald-500/30"
      : status === "failed"
      ? "bg-rose-500/15 text-rose-300 border-rose-500/30"
      : "bg-zinc-500/15 text-zinc-300 border-zinc-500/30";
  return <span className={`px-1.5 py-0.5 rounded text-xs border ${cls}`}>{status}</span>;
}

export function AgentBadge({ agent, round }: { agent: string; round?: number }) {
  return (
    <span className="px-1.5 py-0.5 rounded text-xs bg-indigo-500/15 text-indigo-300 border border-indigo-500/30">
      {agent}
      {round && round > 1 ? <span className="text-indigo-400/70"> · r{round}</span> : null}
    </span>
  );
}

export function fmtTs(s: string | null | undefined) {
  if (!s) return "—";
  try {
    const d = new Date(s);
    return d.toLocaleString();
  } catch {
    return s;
  }
}

export function fmtDur(s: number | null | undefined) {
  if (s == null) return "—";
  if (s < 1) return `${(s * 1000).toFixed(0)}ms`;
  if (s < 60) return `${s.toFixed(1)}s`;
  return `${Math.floor(s / 60)}m ${(s % 60).toFixed(0)}s`;
}

export function Loading() {
  return <div className="text-zinc-500 text-sm py-4">Loading…</div>;
}

export function ErrorBox({ err }: { err: unknown }) {
  return (
    <div className="text-rose-300 text-sm bg-rose-500/10 border border-rose-500/30 rounded p-3">
      {(err as Error).message ?? String(err)}
    </div>
  );
}

export function useFetch<T>(loader: () => Promise<T>, deps: unknown[] = []) {
  const [state, setState] = React.useState<{ data?: T; err?: unknown; loading: boolean }>({ loading: true });
  React.useEffect(() => {
    let cancelled = false;
    setState((s) => ({ ...s, loading: true }));
    loader()
      .then((data) => !cancelled && setState({ data, loading: false }))
      .catch((err) => !cancelled && setState({ err, loading: false }));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return state;
}
