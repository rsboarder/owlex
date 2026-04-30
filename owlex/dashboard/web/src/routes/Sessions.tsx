import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, Session } from "../api";
import { AgentBadge, Card, ErrorBox, Loading, fmtTs } from "../components";

export default function Sessions() {
  const [params] = useSearchParams();
  const since = params.get("since") || undefined;
  const until = params.get("until") || undefined;
  const agreement_min = params.get("agreement_min");
  const agreement_max = params.get("agreement_max");
  const [sessions, setSessions] = useState<Session[] | null>(null);
  const [err, setErr] = useState<unknown>(null);

  useEffect(() => {
    let cancelled = false;
    const tick = () =>
      api
        .sessions({
          limit: 100,
          since,
          until,
          agreement_min: agreement_min ? Number(agreement_min) : undefined,
          agreement_max: agreement_max ? Number(agreement_max) : undefined,
        })
        .then((d) => !cancelled && setSessions(d.sessions))
        .catch((e) => !cancelled && setErr(e));
    tick();
    const id = setInterval(tick, 5000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [since, until, agreement_min, agreement_max]);

  if (err) return <ErrorBox err={err} />;
  if (!sessions) return <Loading />;

  return (
    <Card>
      <div className="px-4 py-3 border-b border-zinc-800 text-sm font-medium flex items-baseline justify-between">
        <span>
          Council sessions <span className="text-zinc-500">({sessions.length})</span>
        </span>
        {(since || until || agreement_min || agreement_max) && (
          <span className="text-xs text-zinc-500">
            filtered:
            {since ? ` since ${since.slice(0, 10)}` : ""}
            {until ? ` until ${until.slice(0, 10)}` : ""}
            {agreement_min ? ` agreement ≥ ${agreement_min}` : ""}
            {agreement_max ? ` ≤ ${agreement_max}` : ""}{" "}
            <Link to="/sessions" className="ml-2 text-emerald-300 hover:underline">
              clear
            </Link>
          </span>
        )}
      </div>
      <table className="w-full text-sm">
        <thead className="text-zinc-400 text-xs uppercase tracking-wider">
          <tr>
            <th className="text-left px-4 py-2">Council</th>
            <th className="text-left px-4 py-2">Started</th>
            <th className="text-right px-4 py-2">Calls</th>
            <th className="text-right px-4 py-2">Failed</th>
            <th className="text-left px-4 py-2">Agents</th>
          </tr>
        </thead>
        <tbody>
          {sessions.map((s) => (
            <tr key={s.council_id} className="border-t border-zinc-800/60 hover:bg-zinc-900/40">
              <td className="px-4 py-2">
                <Link to={`/sessions/${s.council_id}`} className="font-mono text-emerald-300 hover:underline">
                  {s.council_id}
                </Link>
              </td>
              <td className="px-4 py-2 text-zinc-400">{fmtTs(s.started_at)}</td>
              <td className="px-4 py-2 text-right font-mono">{s.calls}</td>
              <td className="px-4 py-2 text-right font-mono text-rose-300/80">{s.failed || ""}</td>
              <td className="px-4 py-2">
                <div className="flex flex-wrap gap-1">
                  {s.agents.filter(Boolean).map((a) => (
                    <AgentBadge key={a} agent={a} />
                  ))}
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}
