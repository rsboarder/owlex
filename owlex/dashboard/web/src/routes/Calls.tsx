import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, Call } from "../api";
import { AgentBadge, Card, ErrorBox, Loading, StatusBadge, fmtDur, fmtTs } from "../components";

const AGENTS = ["", "claudeor", "codex", "gemini", "opencode", "aichat", "cursor"];
const STATUSES = ["", "completed", "failed", "running"];

export default function Calls() {
  const [params, setParams] = useSearchParams();
  const agent = params.get("agent") || "";
  const status = params.get("status") || "";
  const q = params.get("q") || "";
  const sort = params.get("sort") || "";
  const dmin = params.get("duration_min");
  const dmax = params.get("duration_max");
  const hour = params.get("hour");
  const tool = params.get("tool") || "";
  const skill = params.get("skill") || "";

  const [calls, setCalls] = useState<Call[] | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [skillIndex, setSkillIndex] = useState<{
    tools: Array<{ name: string; count: number }>;
    skills: Array<{ name: string; count: number }>;
  } | null>(null);

  useEffect(() => {
    let cancelled = false;
    api.skillNames(agent || undefined).then((d) => !cancelled && setSkillIndex(d)).catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [agent]);

  useEffect(() => {
    let cancelled = false;
    const tick = () =>
      api
        .calls({
          agent: agent || undefined,
          status: status || undefined,
          q: q || undefined,
          sort: sort || undefined,
          duration_min: dmin ? Number(dmin) : undefined,
          duration_max: dmax ? Number(dmax) : undefined,
          hour: hour ? Number(hour) : undefined,
          tool: tool || undefined,
          skill: skill || undefined,
          limit: 200,
        })
        .then((d) => !cancelled && setCalls(d.calls))
        .catch((e) => !cancelled && setErr(e));
    tick();
    const id = setInterval(tick, 5000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [agent, status, q, sort, dmin, dmax, hour, tool, skill]);

  function update(key: string, value: string) {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next);
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-3 items-end">
        <label className="text-sm">
          <div className="text-zinc-400 text-xs uppercase tracking-wider mb-1">Agent</div>
          <select
            value={agent}
            onChange={(e) => update("agent", e.target.value)}
            className="bg-zinc-900 border border-zinc-700 rounded px-2 py-1 text-sm"
          >
            {AGENTS.map((a) => (
              <option key={a} value={a}>
                {a || "all"}
              </option>
            ))}
          </select>
        </label>
        <label className="text-sm">
          <div className="text-zinc-400 text-xs uppercase tracking-wider mb-1">Status</div>
          <select
            value={status}
            onChange={(e) => update("status", e.target.value)}
            className="bg-zinc-900 border border-zinc-700 rounded px-2 py-1 text-sm"
          >
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {s || "all"}
              </option>
            ))}
          </select>
        </label>
        <label className="text-sm">
          <div className="text-zinc-400 text-xs uppercase tracking-wider mb-1">Tool</div>
          <select
            value={tool}
            onChange={(e) => update("tool", e.target.value)}
            className="bg-zinc-900 border border-zinc-700 rounded px-2 py-1 text-sm max-w-[200px]"
          >
            <option value="">all</option>
            {(skillIndex?.tools ?? []).map((t) => (
              <option key={t.name} value={t.name}>
                {t.name} ({t.count})
              </option>
            ))}
          </select>
        </label>
        <label className="text-sm">
          <div className="text-zinc-400 text-xs uppercase tracking-wider mb-1">Skill</div>
          <select
            value={skill}
            onChange={(e) => update("skill", e.target.value)}
            className="bg-zinc-900 border border-zinc-700 rounded px-2 py-1 text-sm max-w-[200px]"
          >
            <option value="">all</option>
            {(skillIndex?.skills ?? []).map((s) => (
              <option key={s.name} value={s.name}>
                {s.name} ({s.count})
              </option>
            ))}
          </select>
        </label>
        <label className="text-sm flex-1 min-w-[200px]">
          <div className="text-zinc-400 text-xs uppercase tracking-wider mb-1">Search preview / error</div>
          <input
            value={q}
            onChange={(e) => update("q", e.target.value)}
            placeholder="text…"
            className="w-full bg-zinc-900 border border-zinc-700 rounded px-2 py-1 text-sm"
          />
        </label>
      </div>

      {(sort || dmin || dmax || hour) && (
        <div className="text-xs text-zinc-500">
          extra filters:
          {sort ? ` sort=${sort}` : ""}
          {dmin ? ` duration ≥ ${dmin}s` : ""}
          {dmax ? ` < ${dmax}s` : ""}
          {hour ? ` hour=${hour}` : ""}{" "}
          <Link
            to="/calls"
            className="ml-2 text-emerald-300 hover:underline"
          >
            clear all
          </Link>
        </div>
      )}

      {err ? (
        <ErrorBox err={err} />
      ) : !calls ? (
        <Loading />
      ) : (
        <Card>
          <div className="px-4 py-3 border-b border-zinc-800 text-sm font-medium">
            Calls <span className="text-zinc-500">({calls.length})</span>
          </div>
          <table className="w-full text-sm">
            <thead className="text-zinc-400 text-xs uppercase tracking-wider">
              <tr>
                <th className="text-left px-4 py-2">Task</th>
                <th className="text-left px-4 py-2">Agent</th>
                <th className="text-left px-4 py-2">Status</th>
                <th className="text-right px-4 py-2">Duration</th>
                <th className="text-left px-4 py-2">Started</th>
                <th className="text-left px-4 py-2">Council</th>
              </tr>
            </thead>
            <tbody>
              {calls.map((c) => (
                <tr key={c.task_id} className="border-t border-zinc-800/60 hover:bg-zinc-900/40">
                  <td className="px-4 py-2">
                    <Link
                      to={`/calls/${c.task_id}`}
                      className="font-mono text-emerald-300 hover:underline"
                    >
                      {c.task_id}
                    </Link>
                  </td>
                  <td className="px-4 py-2">
                    <AgentBadge agent={c.agent} round={c.round} />
                  </td>
                  <td className="px-4 py-2">
                    <StatusBadge status={c.status} />
                  </td>
                  <td className="px-4 py-2 text-right font-mono">{fmtDur(c.duration_s)}</td>
                  <td className="px-4 py-2 text-zinc-400">{fmtTs(c.ts)}</td>
                  <td className="px-4 py-2">
                    {c.council_id && (
                      <Link
                        to={`/sessions/${c.council_id}`}
                        className="font-mono text-zinc-300 hover:underline"
                      >
                        {c.council_id}
                      </Link>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </div>
  );
}
