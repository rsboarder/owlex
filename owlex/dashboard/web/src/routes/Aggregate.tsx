import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, Call, Stats } from "../api";
import { AgentBadge, Card, ErrorBox, Loading, fmtDur } from "../components";
import {
  ActivityChart,
  AgreementChart,
  ChartCard,
  LatencyChart,
  LatencyHeatmap,
  RatingsChart,
  SuccessRateChart,
} from "../charts";

export default function Aggregate() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [live, setLive] = useState<Call[]>([]);
  const [err, setErr] = useState<unknown>(null);

  useEffect(() => {
    let cancelled = false;
    const tick = () => {
      api.stats().then((d) => !cancelled && setStats(d)).catch((e) => !cancelled && setErr(e));
      api.inFlight().then((d) => !cancelled && setLive(d.calls)).catch(() => {});
    };
    tick();
    const id = setInterval(tick, 2000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  if (err) return <ErrorBox err={err} />;
  if (!stats) return <Loading />;

  const failRate = stats.total ? stats.failed / stats.total : 0;

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <Card className="p-4">
          <div className="text-zinc-400 text-xs uppercase tracking-wider">Total calls</div>
          <div className="text-3xl font-mono mt-1">{stats.total.toLocaleString()}</div>
        </Card>
        <Card className="p-4">
          <div className="text-zinc-400 text-xs uppercase tracking-wider">Completed</div>
          <div className="text-3xl font-mono mt-1 text-emerald-300">
            {stats.completed.toLocaleString()}
          </div>
        </Card>
        <Card className="p-4">
          <div className="text-zinc-400 text-xs uppercase tracking-wider">Failed</div>
          <div className="text-3xl font-mono mt-1 text-rose-300">
            {stats.failed.toLocaleString()}
            <span className="text-zinc-500 text-sm ml-2">{(failRate * 100).toFixed(1)}%</span>
          </div>
        </Card>
      </div>

      {live.length > 0 && (
        <Card className="p-4">
          <div className="text-zinc-400 text-xs uppercase tracking-wider mb-3">
            In flight <span className="text-emerald-400">●</span> {live.length}
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
            {live.map((c) => (
              <Link
                key={c.task_id}
                to={`/calls/${c.task_id}`}
                className="border border-emerald-500/30 bg-emerald-500/5 rounded px-3 py-2 hover:bg-emerald-500/10 flex items-center gap-3"
              >
                <span className="relative flex h-2 w-2">
                  <span className="absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75 animate-ping" />
                  <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-400" />
                </span>
                <AgentBadge agent={c.agent} round={c.round} />
                <span className="font-mono text-xs text-zinc-300 flex-1 truncate">{c.task_id}</span>
                <span className="font-mono text-xs text-zinc-400">{fmtDur(c.elapsed_s ?? 0)}</span>
              </Link>
            ))}
          </div>
        </Card>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <ChartCard
          title="Activity (last 30 days)"
          hint="calls per day, stacked by agent"
          description={
            <>
              How busy each agent has been. Heights show <span className="text-zinc-200">total calls per day</span>;
              colored bands break that down by agent. Spikes usually correspond to council bursts — every council
              produces 4–6 calls in parallel.
            </>
          }
        >
          <ActivityChart />
        </ChartCard>
        <ChartCard
          title="Latency"
          hint="p50 · p95 · max per agent"
          description={
            <>
              Wall-clock time per call. <span className="text-emerald-300">p50</span> is the typical case,
              <span className="text-amber-300"> p95</span> is the slow tail, <span className="text-rose-300">max</span> exposes
              outliers (often timeouts). Shorter is better; agents with a long p95 / max gap are the ones that occasionally
              stall the council.
            </>
          }
        >
          <LatencyChart />
        </ChartCard>
        <ChartCard
          title="Success vs failure"
          hint="completed / failed split per agent"
          description={
            <>
              Reliability per agent. <span className="text-emerald-300">Green</span> is completed,
              <span className="text-rose-300"> red</span> is failed (timeout, quota, command-not-found, non-zero exit).
              Use this to spot agents that are flaky enough to disqualify from a tight council.
            </>
          }
        >
          <SuccessRateChart stats={stats} />
        </ChartCard>
        <ChartCard
          title="Latency heatmap"
          hint="hour-of-day × duration bucket · last 30 days"
          description={
            <>
              Reveals cold-start tails that p95 hides. A column with both a bright low-bucket and a bright high-bucket
              cell is bimodal — that agent has two regimes (e.g. warm vs cold start). Click any cell to drill into the
              calls in that bucket.
            </>
          }
        >
          <LatencyHeatmap />
        </ChartCard>
        <ChartCard
          title="Human ratings (last 30 days)"
          hint="thumbs collected on session detail pages"
          description={
            <>
              Direct human signal — open any session and click 👍 or 👎.{" "}
              <span className="text-emerald-300">Green</span> stacks 👍,{" "}
              <span className="text-rose-300">red</span> stacks 👎. Use this to ground-truth the agreement-judge over
              time: high agreement + low ratings = the council reached consensus on a wrong answer.
            </>
          }
        >
          <RatingsChart />
        </ChartCard>
        <ChartCard
          title="Council agreement scores"
          hint="distribution across deliberations · 3.5 triggers R2"
          description={
            <>
              How aligned the agents were after Round 1. Scored 1 (fundamental disagreement) → 5 (full consensus) by an
              LLM judge. <span className="text-zinc-200">Scores below 3.5 trigger Round 2</span> deliberation; the dashed
              line marks that threshold. <span className="text-violet-300">Purple</span> are live councils;
              <span className="text-zinc-400"> gray</span> are historical councils backfilled from truncated previews —
              expect those to read artificially low.
            </>
          }
        >
          <AgreementChart />
        </ChartCard>
      </div>

      <Card>
        <div className="px-4 py-3 border-b border-zinc-800 text-sm font-medium">Per-agent metrics</div>
        <table className="w-full text-sm">
          <thead className="text-zinc-400 text-xs uppercase tracking-wider">
            <tr>
              <th className="text-left px-4 py-2">Agent</th>
              <th className="text-right px-4 py-2">Calls</th>
              <th className="text-right px-4 py-2">Success</th>
              <th className="text-right px-4 py-2">Avg</th>
              <th className="text-right px-4 py-2">p50</th>
              <th className="text-right px-4 py-2">p95</th>
              <th className="text-right px-4 py-2">Failed</th>
            </tr>
          </thead>
          <tbody>
            {stats.by_agent.map((a) => (
              <tr key={a.agent} className="border-t border-zinc-800/60 hover:bg-zinc-900/40">
                <td className="px-4 py-2 font-mono">{a.agent}</td>
                <td className="px-4 py-2 text-right font-mono">{a.total}</td>
                <td className="px-4 py-2 text-right font-mono">
                  <span className={a.success_rate >= 0.9 ? "text-emerald-300" : a.success_rate >= 0.7 ? "text-amber-300" : "text-rose-300"}>
                    {(a.success_rate * 100).toFixed(1)}%
                  </span>
                </td>
                <td className="px-4 py-2 text-right font-mono text-zinc-400">{fmtDur(a.avg_s)}</td>
                <td className="px-4 py-2 text-right font-mono">{fmtDur(a.p50_s)}</td>
                <td className="px-4 py-2 text-right font-mono">{fmtDur(a.p95_s)}</td>
                <td className="px-4 py-2 text-right font-mono text-rose-300/80">{a.failed}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}
