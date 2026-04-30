import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Line, LineChart, ResponsiveContainer } from "recharts";
import { api } from "../api";
import { AgentBadge, Card, ErrorBox, Loading, fmtDur } from "../components";

type SortKey =
  | "rank"
  | "agreement_score"
  | "blind_rating_avg"
  | "success_pct"
  | "p50_s"
  | "p95_s"
  | "total";

const MEDALS = ["🥇", "🥈", "🥉"];

export default function Leaderboard() {
  const [data, setData] = useState<Awaited<ReturnType<typeof api.leaderboard>> | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [sort, setSort] = useState<SortKey>("rank");
  const [asc, setAsc] = useState(true);

  useEffect(() => {
    let cancelled = false;
    api.leaderboard().then((d) => !cancelled && setData(d)).catch((e) => !cancelled && setErr(e));
    return () => {
      cancelled = true;
    };
  }, []);

  const rows = useMemo(() => {
    if (!data) return [];
    const arr = [...data.agents];
    arr.sort((a, b) => {
      const av = (a as any)[sort] ?? 0;
      const bv = (b as any)[sort] ?? 0;
      return asc ? av - bv : bv - av;
    });
    return arr;
  }, [data, sort, asc]);

  function flip(k: SortKey) {
    if (sort === k) setAsc(!asc);
    else {
      setSort(k);
      setAsc(k === "rank" || k === "p50_s" || k === "p95_s");
    }
  }

  if (err) return <ErrorBox err={err} />;
  if (!data) return <Loading />;

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-mono">Agent leaderboard</h1>
        <p className="text-zinc-400 text-sm mt-1">
          Ranked by mean pairwise agreement (council fitness) then success rate. CIs are 95% Wilson intervals.
        </p>
      </div>

      <Card>
        <table className="w-full text-sm">
          <thead className="text-zinc-400 text-xs uppercase tracking-wider">
            <tr>
              <Th onClick={() => flip("rank")}            active={sort === "rank"}            asc={asc}>#</Th>
              <th className="text-left  px-4 py-2">Agent</th>
              <Th onClick={() => flip("agreement_score")} active={sort === "agreement_score"} asc={asc} right>
                Agreement
              </Th>
              <Th onClick={() => flip("blind_rating_avg")} active={sort === "blind_rating_avg"} asc={asc} right>
                Blind rating
              </Th>
              <Th onClick={() => flip("success_pct")}     active={sort === "success_pct"}     asc={asc} right>
                Success ± 95% CI
              </Th>
              <Th onClick={() => flip("p50_s")}           active={sort === "p50_s"}           asc={asc} right>
                p50
              </Th>
              <Th onClick={() => flip("p95_s")}           active={sort === "p95_s"}           asc={asc} right>
                p95
              </Th>
              <Th onClick={() => flip("total")}           active={sort === "total"}           asc={asc} right>
                Calls
              </Th>
              <th className="text-left px-4 py-2">7-day trend</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.agent} className="border-t border-zinc-800/60 hover:bg-zinc-900/40">
                <td className="px-4 py-2 font-mono">
                  {MEDALS[r.rank - 1] ?? r.rank}
                </td>
                <td className="px-4 py-2">
                  <Link to={`/calls?agent=${r.agent}`}>
                    <AgentBadge agent={r.agent} />
                  </Link>
                </td>
                <td className="px-4 py-2 text-right font-mono">
                  {r.agreement_score != null ? (
                    <span
                      className={
                        r.agreement_score >= 4
                          ? "text-emerald-300"
                          : r.agreement_score >= 3
                          ? "text-amber-300"
                          : "text-rose-300"
                      }
                    >
                      {r.agreement_score.toFixed(2)}/5
                    </span>
                  ) : (
                    "—"
                  )}
                </td>
                <td className="px-4 py-2 text-right font-mono">
                  {r.blind_rating_avg != null ? (
                    <div className="leading-tight">
                      <div
                        className={
                          r.blind_rating_avg >= 0.4
                            ? "text-emerald-300"
                            : r.blind_rating_avg <= -0.4
                            ? "text-rose-300"
                            : "text-amber-300"
                        }
                      >
                        {r.blind_rating_avg > 0 ? "+" : ""}
                        {r.blind_rating_avg.toFixed(2)}
                      </div>
                      <div className="text-xs text-zinc-500">n={r.blind_rating_n}</div>
                    </div>
                  ) : (
                    <span className="text-zinc-600">—</span>
                  )}
                </td>
                <td className="px-4 py-2 text-right font-mono">
                  <div className="leading-tight">
                    <div>{r.success_pct.toFixed(1)}%</div>
                    <div className="text-xs text-zinc-500">
                      [{r.ci_low_pct.toFixed(1)}, {r.ci_high_pct.toFixed(1)}]
                    </div>
                  </div>
                </td>
                <td className="px-4 py-2 text-right font-mono">{fmtDur(r.p50_s)}</td>
                <td className="px-4 py-2 text-right font-mono">{fmtDur(r.p95_s)}</td>
                <td className="px-4 py-2 text-right font-mono">{r.total}</td>
                <td className="px-4 py-2" style={{ width: 120 }}>
                  {r.spark.length > 1 ? (
                    <div style={{ width: 100, height: 28 }}>
                      <ResponsiveContainer>
                        <LineChart data={r.spark}>
                          <Line type="monotone" dataKey="calls" stroke="#a78bfa" strokeWidth={1.5} dot={false} />
                        </LineChart>
                      </ResponsiveContainer>
                    </div>
                  ) : (
                    <span className="text-xs text-zinc-500">—</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>

      <ByModelCard />

      <BlindingIntegrityCard />
    </div>
  );
}

function ByModelCard() {
  const [data, setData] = useState<Awaited<ReturnType<typeof api.leaderboardByModel>> | null>(null);
  useEffect(() => {
    let cancelled = false;
    api.leaderboardByModel().then((d) => !cancelled && setData(d)).catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);
  if (!data || data.rows.length === 0) return null;

  return (
    <Card>
      <div className="px-4 py-3 border-b border-zinc-800 text-sm font-medium flex items-baseline justify-between">
        <span>Per-(seat, model) breakdown</span>
        <span className="text-zinc-500 text-xs">
          reveals substitution effects — same seat, different models
        </span>
      </div>
      <table className="w-full text-sm">
        <thead className="text-zinc-400 text-xs uppercase tracking-wider">
          <tr>
            <th className="text-left  px-4 py-2">Seat</th>
            <th className="text-left  px-4 py-2">Model</th>
            <th className="text-right px-4 py-2">Calls</th>
            <th className="text-right px-4 py-2">Avg duration</th>
            <th className="text-right px-4 py-2">Blind avg (n)</th>
          </tr>
        </thead>
        <tbody>
          {data.rows.map((r, i) => (
            <tr key={`${r.agent}-${r.model}-${i}`} className="border-t border-zinc-800/60 hover:bg-zinc-900/40">
              <td className="px-4 py-2">
                <AgentBadge agent={r.agent} />
              </td>
              <td className="px-4 py-2 font-mono text-zinc-300 text-xs">{r.model}</td>
              <td className="px-4 py-2 text-right font-mono">{r.rated_n}</td>
              <td className="px-4 py-2 text-right font-mono text-zinc-400">{fmtDur(r.avg_duration_s)}</td>
              <td className="px-4 py-2 text-right font-mono">
                {r.blind_avg != null ? (
                  <span
                    className={
                      r.blind_avg >= 0.4
                        ? "text-emerald-300"
                        : r.blind_avg <= -0.4
                        ? "text-rose-300"
                        : "text-amber-300"
                    }
                  >
                    {r.blind_avg > 0 ? "+" : ""}
                    {r.blind_avg.toFixed(2)}
                  </span>
                ) : (
                  <span className="text-zinc-600">—</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

function BlindingIntegrityCard() {
  const [data, setData] = useState<Awaited<ReturnType<typeof api.blindIntegrity>> | null>(null);
  useEffect(() => {
    let cancelled = false;
    api.blindIntegrity().then((d) => !cancelled && setData(d)).catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);
  if (!data || data.agents.length === 0) return null;

  const flagged = data.agents.filter((a) => a.deviation != null && Math.abs(a.deviation) > 0.5);

  return (
    <Card>
      <div className="px-4 py-3 border-b border-zinc-800 text-sm font-medium flex items-baseline justify-between">
        <span>Blinding integrity</span>
        <span className="text-zinc-500 text-xs">
          blind rating vs normalized agreement · |deviation| &gt; 0.5 = possible fingerprinting
        </span>
      </div>
      {flagged.length > 0 && (
        <div className="px-4 py-2 text-xs text-amber-300 bg-amber-500/10 border-b border-amber-500/30">
          ⚠ Self-preference detected for: {flagged.map((a) => a.agent).join(", ")}. Consider tightening anonymization.
        </div>
      )}
      <table className="w-full text-sm">
        <thead className="text-zinc-400 text-xs uppercase tracking-wider">
          <tr>
            <th className="text-left px-4 py-2">Agent</th>
            <th className="text-right px-4 py-2">Blind avg (-1..+1)</th>
            <th className="text-right px-4 py-2">Agreement (norm.)</th>
            <th className="text-right px-4 py-2">Deviation</th>
            <th className="text-right px-4 py-2">N</th>
          </tr>
        </thead>
        <tbody>
          {data.agents.map((a) => {
            const dev = a.deviation;
            const devCls =
              dev == null
                ? "text-zinc-500"
                : Math.abs(dev) > 0.5
                ? "text-rose-300"
                : Math.abs(dev) > 0.25
                ? "text-amber-300"
                : "text-emerald-300";
            return (
              <tr key={a.agent} className="border-t border-zinc-800/60 hover:bg-zinc-900/40">
                <td className="px-4 py-2">
                  <AgentBadge agent={a.agent} />
                </td>
                <td className="px-4 py-2 text-right font-mono">
                  {a.blind_avg > 0 ? "+" : ""}
                  {a.blind_avg.toFixed(2)}
                </td>
                <td className="px-4 py-2 text-right font-mono">
                  {a.agreement_norm != null ? (
                    <>
                      {a.agreement_norm > 0 ? "+" : ""}
                      {a.agreement_norm.toFixed(2)}
                    </>
                  ) : (
                    "—"
                  )}
                </td>
                <td className={`px-4 py-2 text-right font-mono ${devCls}`}>
                  {dev != null ? (dev > 0 ? "+" : "") + dev.toFixed(2) : "—"}
                </td>
                <td className="px-4 py-2 text-right font-mono text-zinc-400">{a.blind_n}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </Card>
  );
}

function Th({
  children,
  active,
  asc,
  onClick,
  right,
}: {
  children: React.ReactNode;
  active: boolean;
  asc: boolean;
  onClick: () => void;
  right?: boolean;
}) {
  return (
    <th
      onClick={onClick}
      className={`px-4 py-2 cursor-pointer select-none hover:text-zinc-200 ${right ? "text-right" : "text-left"} ${
        active ? "text-zinc-100" : ""
      }`}
    >
      {children}
      {active ? <span className="ml-1">{asc ? "▲" : "▼"}</span> : null}
    </th>
  );
}
