import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api, Stats } from "./api";
import { Card, Loading } from "./components";

const AGENT_COLORS: Record<string, string> = {
  claudeor: "#a78bfa",
  codex:    "#34d399",
  gemini:   "#60a5fa",
  opencode: "#f472b6",
  aichat:   "#fbbf24",
  cursor:   "#fb7185",
};

const tooltipStyle = {
  backgroundColor: "rgba(24, 24, 27, 0.95)",
  border: "1px solid rgb(63, 63, 70)",
  borderRadius: "6px",
  fontSize: "12px",
  fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
};

export function ActivityChart() {
  const [data, setData] = useState<Array<Record<string, any>> | null>(null);
  const navigate = useNavigate();

  useEffect(() => {
    let cancelled = false;
    const tick = () =>
      api.timeseries(30).then((d) => !cancelled && setData(d.days)).catch(() => {});
    tick();
    const id = setInterval(tick, 10_000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  if (!data) return <Loading />;
  if (data.length === 0)
    return <div className="px-4 py-8 text-zinc-500 text-sm">No activity in the last 30 days.</div>;

  const agents = Object.keys(AGENT_COLORS);

  return (
    <div className="h-72 px-2">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart
          data={data}
          margin={{ top: 10, right: 16, left: 0, bottom: 0 }}
          onClick={(s: any) => {
            const day = s?.activeLabel;
            if (day) navigate(`/sessions?since=${day}&until=${day}`);
          }}
        >
          <defs>
            {agents.map((a) => (
              <linearGradient key={a} id={`grad-${a}`} x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%"  stopColor={AGENT_COLORS[a]} stopOpacity={0.7} />
                <stop offset="95%" stopColor={AGENT_COLORS[a]} stopOpacity={0.05} />
              </linearGradient>
            ))}
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="rgb(39, 39, 42)" />
          <XAxis dataKey="day" stroke="rgb(161, 161, 170)" fontSize={11} tickFormatter={(d) => d.slice(5)} />
          <YAxis stroke="rgb(161, 161, 170)" fontSize={11} />
          <Tooltip contentStyle={tooltipStyle} labelStyle={{ color: "rgb(161, 161, 170)" }} />
          <Legend wrapperStyle={{ fontSize: 12 }} iconType="circle" />
          {agents.map((a) => (
            <Area
              key={a}
              type="monotone"
              dataKey={a}
              stackId="1"
              stroke={AGENT_COLORS[a]}
              fill={`url(#grad-${a})`}
              strokeWidth={1.5}
            />
          ))}
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

export function LatencyChart() {
  const [data, setData] = useState<
    Array<{ agent: string; p50: number; p95: number; max: number; n: number }> | null
  >(null);
  const navigate = useNavigate();

  useEffect(() => {
    let cancelled = false;
    api
      .latencyDist()
      .then((d) => !cancelled && setData(d.by_agent))
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  if (!data) return <Loading />;
  if (data.length === 0) return <div className="px-4 py-8 text-zinc-500 text-sm">No completed calls yet.</div>;

  return (
    <div className="h-72 px-2">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart
          data={data}
          layout="vertical"
          margin={{ top: 10, right: 24, left: 8, bottom: 0 }}
          onClick={(s: any) => {
            const a = s?.activePayload?.[0]?.payload?.agent;
            if (a) navigate(`/calls?agent=${a}&sort=duration_desc`);
          }}
        >
          <CartesianGrid strokeDasharray="3 3" stroke="rgb(39, 39, 42)" horizontal={false} />
          <XAxis
            type="number"
            stroke="rgb(161, 161, 170)"
            fontSize={11}
            tickFormatter={(s) => `${s}s`}
          />
          <YAxis dataKey="agent" type="category" stroke="rgb(161, 161, 170)" fontSize={12} width={80} />
          <Tooltip
            contentStyle={tooltipStyle}
            labelStyle={{ color: "rgb(161, 161, 170)" }}
            formatter={(v: number, name: string) => [`${v}s`, name]}
          />
          <Legend wrapperStyle={{ fontSize: 12 }} iconType="circle" />
          <Bar dataKey="p50" name="p50" fill="#34d399" radius={[0, 4, 4, 0]} />
          <Bar dataKey="p95" name="p95" fill="#fbbf24" radius={[0, 4, 4, 0]} />
          <Bar dataKey="max" name="max" fill="#fb7185" radius={[0, 4, 4, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

export function SuccessRateChart({ stats }: { stats: Stats }) {
  const navigate = useNavigate();
  if (!stats || stats.by_agent.length === 0) return null;
  const data = stats.by_agent
    .slice()
    .sort((a, b) => b.success_rate - a.success_rate)
    .map((s) => ({
      agent: s.agent,
      success: s.completed,
      fail: s.failed,
      rate: Math.round(s.success_rate * 1000) / 10,
    }));

  return (
    <div className="h-72 px-2">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart
          data={data}
          margin={{ top: 10, right: 16, left: 0, bottom: 0 }}
          onClick={(s: any) => {
            const a = s?.activePayload?.[0]?.payload?.agent;
            const dataKey = s?.activePayload?.[0]?.dataKey;
            if (a) {
              const status = dataKey === "fail" ? "failed" : "completed";
              navigate(`/calls?agent=${a}&status=${status}`);
            }
          }}
        >
          <CartesianGrid strokeDasharray="3 3" stroke="rgb(39, 39, 42)" />
          <XAxis dataKey="agent" stroke="rgb(161, 161, 170)" fontSize={12} />
          <YAxis stroke="rgb(161, 161, 170)" fontSize={11} />
          <Tooltip
            contentStyle={tooltipStyle}
            labelStyle={{ color: "rgb(161, 161, 170)" }}
            formatter={(v: number, name: string) => [v, name]}
          />
          <Legend wrapperStyle={{ fontSize: 12 }} iconType="circle" />
          <Bar dataKey="success" name="completed" stackId="a" fill="#34d399" radius={[0, 0, 0, 0]} />
          <Bar dataKey="fail" name="failed" stackId="a" fill="#fb7185" radius={[4, 4, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

export function AgreementChart() {
  const [data, setData] = useState<
    | {
        buckets: Array<{ bin: number; live: number; backfilled: number; count: number }>;
        total: number;
        live: number;
        backfilled: number;
      }
    | null
  >(null);
  const navigate = useNavigate();

  useEffect(() => {
    let cancelled = false;
    const tick = () => api.agreementDist().then((d) => !cancelled && setData(d)).catch(() => {});
    tick();
    const id = setInterval(tick, 10_000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  if (!data) return <Loading />;
  if (data.total === 0)
    return (
      <div className="px-4 py-10 text-center text-zinc-500 text-sm">
        No agreement data yet — it appears after the next council deliberation.
      </div>
    );

  return (
    <div className="h-72 px-2">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart
          data={data.buckets}
          margin={{ top: 10, right: 16, left: 0, bottom: 0 }}
          onClick={(s: any) => {
            const bin = s?.activePayload?.[0]?.payload?.bin;
            if (typeof bin === "number")
              navigate(`/sessions?agreement_min=${(bin - 0.25).toFixed(2)}&agreement_max=${(bin + 0.25).toFixed(2)}`);
          }}
        >
          <CartesianGrid strokeDasharray="3 3" stroke="rgb(39, 39, 42)" />
          <XAxis dataKey="bin" stroke="rgb(161, 161, 170)" fontSize={11} />
          <YAxis stroke="rgb(161, 161, 170)" fontSize={11} allowDecimals={false} />
          <Tooltip contentStyle={tooltipStyle} labelStyle={{ color: "rgb(161, 161, 170)" }} />
          <Legend wrapperStyle={{ fontSize: 12 }} iconType="circle" />
          <ReferenceLine
            x={3.5}
            stroke="rgb(161, 161, 170)"
            strokeDasharray="3 3"
            label={{ value: "R2 trigger", fill: "rgb(161, 161, 170)", fontSize: 10 }}
          />
          <Bar dataKey="backfilled" name="backfilled" stackId="g" fill="#71717a" radius={[0, 0, 0, 0]} />
          <Bar dataKey="live" name="live" stackId="g" fill="#a78bfa" radius={[4, 4, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

export function RatingsChart() {
  const [rater, setRater] = useState<string>("");
  const [data, setData] = useState<Array<{ day: string; up: number; down: number }> | null>(null);

  useEffect(() => {
    let cancelled = false;
    const tick = () =>
      api
        .scoresTimeseries(30, rater || undefined)
        .then((d) => !cancelled && setData(d.days))
        .catch(() => {});
    tick();
    const id = setInterval(tick, 10_000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [rater]);

  return (
    <div className="px-2">
      <div className="flex items-center justify-end gap-2 mb-2">
        <label className="text-xs text-zinc-400">rater</label>
        <select
          value={rater}
          onChange={(e) => setRater(e.target.value)}
          className="bg-zinc-900 border border-zinc-700 rounded px-2 py-1 text-xs"
        >
          <option value="">all</option>
          <option value="human">human</option>
          <option value="claude">claude</option>
          <option value="claude_blind">claude_blind</option>
        </select>
      </div>
      {!data ? (
        <Loading />
      ) : data.length === 0 ? (
        <div className="px-4 py-10 text-center text-zinc-500 text-sm">
          No ratings yet for this rater filter.
        </div>
      ) : (
        <div className="h-64">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={data} margin={{ top: 10, right: 16, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgb(39, 39, 42)" />
              <XAxis dataKey="day" stroke="rgb(161, 161, 170)" fontSize={11} tickFormatter={(d) => d.slice(5)} />
              <YAxis stroke="rgb(161, 161, 170)" fontSize={11} allowDecimals={false} />
              <Tooltip contentStyle={tooltipStyle} labelStyle={{ color: "rgb(161, 161, 170)" }} />
              <Legend wrapperStyle={{ fontSize: 12 }} iconType="circle" />
              <Bar dataKey="up" name="👍 thumbs up" stackId="r" fill="#34d399" radius={[0, 0, 0, 0]} />
              <Bar dataKey="down" name="👎 thumbs down" stackId="r" fill="#fb7185" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}

export function AgreementMatrix({ cid }: { cid: string }) {
  const [data, setData] = useState<{
    participants: string[];
    cells: Array<Array<number | null>>;
    sources: Array<Array<string | null>>;
    reasons: Array<Array<string | null>>;
  } | null>(null);

  useEffect(() => {
    let cancelled = false;
    api.matrix(cid).then((d) => !cancelled && setData(d)).catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [cid]);

  if (!data) return <Loading />;
  if (data.participants.length === 0)
    return (
      <div className="px-4 py-6 text-zinc-500 text-sm">
        No pairwise data for this council yet. Run{" "}
        <code className="font-mono text-zinc-300">python -m owlex.dashboard.backfill --pairwise</code>{" "}
        to populate legacy councils, or wait for the next live one.
      </div>
    );

  const n = data.participants.length;
  const cell = 56;
  const labelW = 84;
  const labelH = 28;
  const W = labelW + n * cell;
  const H = labelH + n * cell;

  function fillFor(score: number | null): string {
    if (score == null) return "rgb(39, 39, 42)";
    // 1..5 → red..amber..green
    if (score >= 4) return "#34d399";
    if (score >= 3) return "#fbbf24";
    if (score >= 2) return "#fb923c";
    return "#fb7185";
  }

  return (
    <div className="px-4 py-2 overflow-auto">
      <svg width={W} height={H} className="font-mono text-xs">
        {data.participants.map((p, i) => (
          <text
            key={`col-${p}`}
            x={labelW + i * cell + cell / 2}
            y={labelH - 8}
            textAnchor="middle"
            fill="rgb(212, 212, 216)"
          >
            {p}
          </text>
        ))}
        {data.participants.map((p, i) => (
          <text
            key={`row-${p}`}
            x={labelW - 8}
            y={labelH + i * cell + cell / 2 + 4}
            textAnchor="end"
            fill="rgb(212, 212, 216)"
          >
            {p}
          </text>
        ))}
        {data.cells.map((row, i) =>
          row.map((score, j) => {
            const src = data.sources[i][j];
            const reason = data.reasons[i][j];
            return (
              <g key={`${i}-${j}`}>
                <rect
                  x={labelW + j * cell + 2}
                  y={labelH + i * cell + 2}
                  width={cell - 4}
                  height={cell - 4}
                  rx={4}
                  fill={fillFor(score)}
                  fillOpacity={i === j ? 0.25 : 0.85}
                  stroke={i === j ? "rgb(82, 82, 91)" : "transparent"}
                >
                  {(score != null || reason) && (
                    <title>
                      {data.participants[i]} ↔ {data.participants[j]}
                      {score != null ? `\nscore: ${score.toFixed(1)}/5` : ""}
                      {src ? `\nsource: ${src}` : ""}
                      {reason ? `\nreason: ${reason}` : ""}
                    </title>
                  )}
                </rect>
                {score != null && i !== j && (
                  <text
                    x={labelW + j * cell + cell / 2}
                    y={labelH + i * cell + cell / 2 + 4}
                    textAnchor="middle"
                    fill="rgb(24, 24, 27)"
                    style={{ fontWeight: 600 }}
                  >
                    {score.toFixed(1)}
                  </text>
                )}
                {src === "overlap" && i !== j && (
                  <circle
                    cx={labelW + j * cell + cell - 8}
                    cy={labelH + i * cell + 8}
                    r={3}
                    fill="none"
                    stroke="rgb(24, 24, 27)"
                    strokeWidth={1}
                  />
                )}
                {src === "judge" && i !== j && (
                  <circle
                    cx={labelW + j * cell + cell - 8}
                    cy={labelH + i * cell + 8}
                    r={3}
                    fill="rgb(24, 24, 27)"
                  />
                )}
              </g>
            );
          })
        )}
      </svg>
      <div className="mt-2 text-xs text-zinc-500 flex gap-4">
        <span><span className="inline-block w-2 h-2 rounded-full bg-zinc-300 mr-1" />judge-scored</span>
        <span><span className="inline-block w-2 h-2 rounded-full border border-zinc-300 mr-1" />overlap (backfill)</span>
        <span className="ml-auto text-zinc-400">cell color = agreement (red 1 → green 5)</span>
      </div>
    </div>
  );
}

export function VoteFlipTimeline({ cid }: { cid: string }) {
  const [data, setData] = useState<
    | {
        agents: Array<{
          agent: string;
          r1: any;
          r2: any;
        }>;
      }
    | null
  >(null);

  useEffect(() => {
    let cancelled = false;
    api.timeline(cid).then((d) => !cancelled && setData(d)).catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [cid]);

  if (!data) return <Loading />;
  if (data.agents.length === 0)
    return <div className="px-4 py-6 text-zinc-500 text-sm">No participants for this council.</div>;

  const hasR2 = data.agents.some((a) => a.r2);
  if (!hasR2) {
    return (
      <div className="px-4 py-6 text-zinc-500 text-sm">
        Round 2 was skipped (auto-deliberation found consensus). Nothing to flip.
      </div>
    );
  }

  // Compute time range
  const all: number[] = [];
  for (const a of data.agents) {
    if (a.r1?.started_at) all.push(new Date(a.r1.started_at).getTime());
    if (a.r1?.completed_at) all.push(new Date(a.r1.completed_at).getTime());
    if (a.r2?.started_at) all.push(new Date(a.r2.started_at).getTime());
    if (a.r2?.completed_at) all.push(new Date(a.r2.completed_at).getTime());
  }
  if (all.length === 0) return <div className="px-4 py-6 text-zinc-500 text-sm">No timing.</div>;
  const t0 = Math.min(...all);
  const t1 = Math.max(...all);
  const span = Math.max(1, t1 - t0);

  const W = 720;
  const labelW = 96;
  const rowH = 40;
  const padX = 32;
  const innerW = W - labelW - padX * 2;
  const H = 24 + data.agents.length * rowH + 24;
  const xOf = (ts: string) => labelW + padX + ((new Date(ts).getTime() - t0) / span) * innerW;

  const colorFor = (label?: string) =>
    label === "major" ? "#fb7185" : label === "minor" ? "#fbbf24" : label === "unchanged" ? "#34d399" : "rgb(113, 113, 122)";

  return (
    <div className="px-4 py-2 overflow-auto">
      <svg width={W} height={H} className="font-mono text-xs">
        {/* baseline ticks */}
        <line x1={labelW + padX} x2={W - padX} y1={H - 14} y2={H - 14} stroke="rgb(63, 63, 70)" />
        <text x={labelW + padX} y={H - 2} fill="rgb(113, 113, 122)">
          {new Date(t0).toLocaleTimeString()}
        </text>
        <text x={W - padX} y={H - 2} textAnchor="end" fill="rgb(113, 113, 122)">
          {new Date(t1).toLocaleTimeString()}
        </text>
        {data.agents.map((a, i) => {
          const y = 18 + i * rowH + rowH / 2;
          const r1x = a.r1 ? xOf(a.r1.started_at) : null;
          const r2x = a.r2 ? xOf(a.r2.started_at) : null;
          const color = colorFor(a.r2?.position_label);
          const stroke = a.r2?.position_delta != null ? 1 + (a.r2.position_delta as number) * 6 : 2;
          return (
            <g key={a.agent}>
              <text x={labelW - 8} y={y + 4} textAnchor="end" fill="rgb(212, 212, 216)">
                {a.agent}
              </text>
              {/* swimlane */}
              <line
                x1={labelW + padX}
                x2={W - padX}
                y1={y}
                y2={y}
                stroke="rgb(39, 39, 42)"
                strokeDasharray="3 3"
              />
              {r1x != null && (
                <circle cx={r1x} cy={y} r={6} fill="#a78bfa">
                  <title>
                    R1 · {a.agent} · {a.r1?.duration_s?.toFixed(1)}s · {a.r1?.status}
                  </title>
                </circle>
              )}
              {r2x != null && (
                <circle cx={r2x} cy={y} r={6} fill={color}>
                  <title>
                    R2 · {a.agent} · {a.r2?.duration_s?.toFixed(1)}s · {a.r2?.status}
                    {a.r2?.position_label
                      ? ` · ${a.r2.position_label} (${((a.r2.position_delta as number) * 100).toFixed(0)}% shift)`
                      : ""}
                  </title>
                </circle>
              )}
              {r1x != null && r2x != null && (
                <path
                  d={`M ${r1x} ${y} Q ${(r1x + r2x) / 2} ${y - 22} ${r2x} ${y}`}
                  stroke={color}
                  strokeWidth={stroke}
                  fill="none"
                  opacity={0.85}
                />
              )}
            </g>
          );
        })}
      </svg>
      <div className="mt-2 text-xs text-zinc-500 flex gap-4">
        <span><span className="inline-block w-2 h-2 rounded-full bg-violet-400 mr-1" />R1</span>
        <span><span className="inline-block w-2 h-2 rounded-full bg-emerald-400 mr-1" />unchanged</span>
        <span><span className="inline-block w-2 h-2 rounded-full bg-amber-400 mr-1" />minor shift</span>
        <span><span className="inline-block w-2 h-2 rounded-full bg-rose-400 mr-1" />major shift</span>
        <span className="ml-auto text-zinc-400">arc thickness ∝ Jaccard distance R1 → R2</span>
      </div>
    </div>
  );
}

export function LatencyHeatmap() {
  const [agent, setAgent] = useState<string>("");
  const [data, setData] = useState<{
    hours: number[];
    buckets: Array<{ label: string; lo: number; hi: number }>;
    grid: number[][];
    total: number;
  } | null>(null);
  const navigate = useNavigate();

  useEffect(() => {
    let cancelled = false;
    api
      .latencyHeatmap(agent || undefined, 30)
      .then((d) => !cancelled && setData(d))
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [agent]);

  if (!data) return <Loading />;
  if (data.total === 0)
    return <div className="px-4 py-10 text-center text-zinc-500 text-sm">No completed calls in the last 30 days.</div>;

  // Normalize per row (per duration bucket) so each bucket's color scale is independent.
  const rowMax = data.grid.map((row) => Math.max(1, ...row));
  const cellW = 28;
  const cellH = 22;
  const labelW = 80;
  const labelH = 22;
  const W = labelW + data.hours.length * cellW + 8;
  const H = labelH + data.buckets.length * cellH + 22;

  function onCellClick(hour: number, lo: number, hi: number) {
    const params = new URLSearchParams();
    params.set("status", "completed");
    if (agent) params.set("agent", agent);
    params.set("duration_min", String(lo));
    params.set("duration_max", String(hi));
    params.set("hour", String(hour));
    navigate(`/calls?${params.toString()}`);
  }

  return (
    <div className="px-4 py-2 overflow-auto">
      <div className="flex items-center justify-end gap-2 mb-2">
        <label className="text-xs text-zinc-400">agent</label>
        <select
          value={agent}
          onChange={(e) => setAgent(e.target.value)}
          className="bg-zinc-900 border border-zinc-700 rounded px-2 py-1 text-xs"
        >
          <option value="">all</option>
          {Object.keys(AGENT_COLORS).map((a) => (
            <option key={a} value={a}>
              {a}
            </option>
          ))}
        </select>
      </div>
      <svg width={W} height={H} className="font-mono text-xs">
        {data.hours.map((h, i) => (
          <text
            key={`h-${h}`}
            x={labelW + i * cellW + cellW / 2}
            y={labelH - 6}
            textAnchor="middle"
            fill="rgb(161, 161, 170)"
          >
            {String(h).padStart(2, "0")}
          </text>
        ))}
        {data.buckets.map((b, bi) => (
          <text
            key={`b-${bi}`}
            x={labelW - 6}
            y={labelH + bi * cellH + cellH / 2 + 4}
            textAnchor="end"
            fill="rgb(212, 212, 216)"
          >
            {b.label}
          </text>
        ))}
        {data.grid.map((row, bi) =>
          row.map((count, hi) => {
            const max = rowMax[bi];
            const intensity = count / max;
            const fill = `rgba(110, 231, 183, ${0.08 + intensity * 0.85})`;
            return (
              <g key={`${bi}-${hi}`}>
                <rect
                  x={labelW + hi * cellW + 1}
                  y={labelH + bi * cellH + 1}
                  width={cellW - 2}
                  height={cellH - 2}
                  rx={2}
                  fill={count === 0 ? "rgb(39, 39, 42)" : fill}
                  style={{ cursor: count === 0 ? "default" : "pointer" }}
                  onClick={() => count > 0 && onCellClick(hi, data.buckets[bi].lo, data.buckets[bi].hi)}
                >
                  <title>
                    {data.buckets[bi].label} · {String(hi).padStart(2, "0")}:00 · {count} calls
                  </title>
                </rect>
                {count > 0 && (
                  <text
                    x={labelW + hi * cellW + cellW / 2}
                    y={labelH + bi * cellH + cellH / 2 + 4}
                    textAnchor="middle"
                    fill="rgb(24, 24, 27)"
                    style={{ pointerEvents: "none", fontWeight: 600, fontSize: 10 }}
                  >
                    {count}
                  </text>
                )}
              </g>
            );
          })
        )}
      </svg>
      <div className="mt-2 text-xs text-zinc-500 flex justify-between">
        <span>X = hour of day · Y = duration bucket</span>
        <span>color shaded per row · click cell → filtered call list</span>
      </div>
    </div>
  );
}

export function SpanTree({ cid }: { cid: string }) {
  const [data, setData] = useState<any>(null);

  useEffect(() => {
    let cancelled = false;
    api.tree(cid).then((d) => !cancelled && setData(d)).catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [cid]);

  if (!data) return <Loading />;
  if (!data.started_at || !data.ended_at)
    return <div className="px-4 py-6 text-zinc-500 text-sm">Session has no timing data.</div>;

  const t0 = new Date(data.started_at).getTime();
  const t1 = new Date(data.ended_at).getTime();
  const span = Math.max(1, t1 - t0);

  const W = 880;
  const labelW = 200;
  const padX = 16;
  const innerW = W - labelW - padX * 2;
  const rowH = 26;
  const rounds = data.rounds as any[];
  const totalRows = 1 + rounds.length + rounds.reduce((acc: number, r: any) => acc + r.calls.length, 0);
  const H = 16 + totalRows * rowH + 14;

  const xOf = (ts: string) => labelW + padX + ((new Date(ts).getTime() - t0) / span) * innerW;
  const wOf = (start: string, end: string | null, durSec: number | null) => {
    const startMs = new Date(start).getTime();
    const endMs = end ? new Date(end).getTime() : startMs + (durSec ?? 0) * 1000;
    return Math.max(2, ((endMs - startMs) / span) * innerW);
  };

  const colorByStatus = (s: string) =>
    s === "completed" ? "#34d399" : s === "failed" ? "#fb7185" : s === "running" ? "#a78bfa" : "rgb(113, 113, 122)";

  let row = 0;
  const sessionRow = row++;
  return (
    <div className="px-4 py-3 overflow-auto">
      <svg width={W} height={H} className="font-mono text-xs">
        {/* Session row */}
        <text x={labelW - 8} y={16 + sessionRow * rowH + rowH / 2 + 4} textAnchor="end" fill="rgb(212, 212, 216)">
          session {data.council_id}
        </text>
        <rect
          x={labelW + padX}
          y={16 + sessionRow * rowH + 4}
          width={innerW}
          height={rowH - 8}
          rx={3}
          fill="rgb(63, 63, 70)"
        />
        {rounds.map((rd: any) => {
          const roundRow = row++;
          return (
            <g key={`round-${rd.round}`}>
              <text
                x={labelW - 8}
                y={16 + roundRow * rowH + rowH / 2 + 4}
                textAnchor="end"
                fill="rgb(161, 161, 170)"
              >
                Round {rd.round}
              </text>
              {rd.calls.map((c: any) => {
                const callRow = row++;
                const x = xOf(c.started_at);
                const w = wOf(c.started_at, c.completed_at, c.duration_s);
                const y = 16 + callRow * rowH;
                return (
                  <g key={c.task_id}>
                    <text x={labelW - 8} y={y + rowH / 2 + 4} textAnchor="end" fill="rgb(212, 212, 216)">
                      {c.agent}
                    </text>
                    <rect
                      x={x}
                      y={y + 4}
                      width={w}
                      height={rowH - 8}
                      rx={3}
                      fill={colorByStatus(c.status)}
                      fillOpacity={0.85}
                      onClick={() => (window.location.href = `/calls/${c.task_id}`)}
                      style={{ cursor: "pointer" }}
                    >
                      <title>
                        {c.agent} · {c.status} · {(c.duration_s ?? 0).toFixed(1)}s · {c.skills.length} tool calls
                      </title>
                    </rect>
                    {/* skills as ticks */}
                    {c.skills.length > 0 &&
                      c.skills.map((s: any, i: number) => {
                        const tickX = x + ((i + 0.5) / c.skills.length) * w;
                        return (
                          <line
                            key={s.seq}
                            x1={tickX}
                            x2={tickX}
                            y1={y + 6}
                            y2={y + rowH - 6}
                            stroke="rgb(24, 24, 27)"
                            strokeOpacity={0.7}
                            strokeWidth={1}
                          >
                            <title>
                              {s.kind}: {s.name}
                              {s.args_summary ? `\n${s.args_summary}` : ""}
                            </title>
                          </line>
                        );
                      })}
                    <text
                      x={x + w + 6}
                      y={y + rowH / 2 + 4}
                      fill="rgb(161, 161, 170)"
                      style={{ pointerEvents: "none" }}
                    >
                      {(c.duration_s ?? 0).toFixed(1)}s
                      {c.skills.length > 0 ? `  ·  ${c.skills.length}` : ""}
                    </text>
                  </g>
                );
              })}
            </g>
          );
        })}
      </svg>
      <div className="mt-2 text-xs text-zinc-500 flex gap-4">
        <span><span className="inline-block w-2 h-2 rounded-full bg-emerald-400 mr-1" />completed</span>
        <span><span className="inline-block w-2 h-2 rounded-full bg-rose-400 mr-1" />failed</span>
        <span><span className="inline-block w-2 h-2 rounded-full bg-violet-400 mr-1" />running</span>
        <span className="ml-auto text-zinc-400">click a bar → call detail · ticks = tool/skill invocations</span>
      </div>
    </div>
  );
}

export function ChartCard({
  title,
  hint,
  description,
  children,
}: {
  title: string;
  hint?: string;
  description?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <Card className="flex flex-col">
      <div className="px-4 py-3 border-b border-zinc-800 text-sm font-medium flex items-baseline justify-between">
        <span>{title}</span>
        {hint ? <span className="text-zinc-500 text-xs">{hint}</span> : null}
      </div>
      <div className="py-3">{children}</div>
      {description ? (
        <div className="px-4 py-3 border-t border-zinc-800/60 text-xs text-zinc-400 leading-relaxed">
          {description}
        </div>
      ) : null}
    </Card>
  );
}
