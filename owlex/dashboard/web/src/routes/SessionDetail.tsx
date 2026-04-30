import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { AgentAnswers, AnswerPane as AnswerPaneT, api } from "../api";
import { AgreementMatrix, SpanTree, VoteFlipTimeline } from "../charts";
import { AgentBadge, Card, ErrorBox, Loading, StatusBadge, fmtDur, fmtTs, useFetch } from "../components";

type AgentScore = {
  agent: string;
  rater: string;
  score: -1 | 1;
  dimensions: Record<string, number> | null;
  reason: string | null;
  ts: string;
};

function AgentRatingsPanel({ cid }: { cid: string }) {
  const [ratings, setRatings] = useState<AgentScore[] | null>(null);
  useEffect(() => {
    let cancelled = false;
    api.agentScores(cid).then((d) => !cancelled && setRatings(d.ratings)).catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [cid]);

  if (!ratings || ratings.length === 0) return null;

  return (
    <Card>
      <div className="px-4 py-3 border-b border-zinc-800 text-sm font-medium flex items-baseline justify-between">
        <span>Per-agent ratings</span>
        <span className="text-zinc-500 text-xs">
          submitted by orchestrator under blind labels (mapped server-side)
        </span>
      </div>
      <table className="w-full text-sm">
        <thead className="text-zinc-400 text-xs uppercase tracking-wider">
          <tr>
            <th className="text-left  px-4 py-2">Agent</th>
            <th className="text-right px-4 py-2">Score</th>
            <th className="text-right px-4 py-2">Groundedness</th>
            <th className="text-right px-4 py-2">Helpfulness</th>
            <th className="text-right px-4 py-2">Correctness</th>
            <th className="text-left  px-4 py-2">Reason</th>
            <th className="text-left  px-4 py-2">Rater</th>
          </tr>
        </thead>
        <tbody>
          {ratings.map((r, i) => {
            const d = r.dimensions || {};
            return (
              <tr key={`${r.agent}-${i}`} className="border-t border-zinc-800/60 hover:bg-zinc-900/40">
                <td className="px-4 py-2">
                  <AgentBadge agent={r.agent} />
                </td>
                <td className="px-4 py-2 text-right font-mono">
                  <span className={r.score === 1 ? "text-emerald-300" : "text-rose-300"}>
                    {r.score === 1 ? "👍 +1" : "👎 −1"}
                  </span>
                </td>
                <td className="px-4 py-2 text-right font-mono">{d.groundedness ?? "—"}</td>
                <td className="px-4 py-2 text-right font-mono">{d.helpfulness ?? "—"}</td>
                <td className="px-4 py-2 text-right font-mono">{d.correctness ?? "—"}</td>
                <td className="px-4 py-2 text-zinc-300">{r.reason || ""}</td>
                <td className="px-4 py-2 text-xs text-zinc-500 font-mono">{r.rater}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </Card>
  );
}

export default function SessionDetail() {
  const { cid = "" } = useParams();
  const { data, err, loading } = useFetch(() => api.session(cid), [cid]);
  const [scoring, setScoring] = useState<null | 1 | -1>(null);
  const [comment, setComment] = useState("");
  const [scoreError, setScoreError] = useState<string | null>(null);
  const [submitted, setSubmitted] = useState<null | 1 | -1>(null);

  async function rate(score: 1 | -1) {
    setScoring(score);
    setScoreError(null);
    try {
      await api.scoreSession(cid, score, comment || undefined);
      setSubmitted(score);
      setComment("");
    } catch (e) {
      setScoreError((e as Error).message);
    } finally {
      setScoring(null);
    }
  }

  if (loading) return <Loading />;
  if (err) return <ErrorBox err={err} />;
  if (!data) return null;

  const lastScore = submitted ?? (data.scores.length ? data.scores[data.scores.length - 1].score : null);

  const r1 = data.calls.filter((c) => c.round === 1);
  const r2 = data.calls.filter((c) => c.round === 2);

  return (
    <div className="space-y-6">
      <div>
        <Link to="/sessions" className="text-zinc-400 hover:text-white text-sm">
          ← all sessions
        </Link>
        <div className="flex flex-wrap items-center gap-3 mt-1">
          <h1 className="text-2xl font-mono">council {data.council_id}</h1>
          <div className="flex items-center gap-2 ml-auto">
            <button
              onClick={() => rate(1)}
              disabled={scoring !== null}
              className={`px-3 py-1 rounded border text-sm transition ${
                lastScore === 1
                  ? "bg-emerald-500/20 border-emerald-500/50 text-emerald-200"
                  : "border-zinc-700 hover:border-emerald-500/50 hover:bg-emerald-500/10"
              } disabled:opacity-50`}
              title="This council was helpful"
            >
              👍 {data.scores.filter((s) => s.score === 1).length || ""}
            </button>
            <button
              onClick={() => rate(-1)}
              disabled={scoring !== null}
              className={`px-3 py-1 rounded border text-sm transition ${
                lastScore === -1
                  ? "bg-rose-500/20 border-rose-500/50 text-rose-200"
                  : "border-zinc-700 hover:border-rose-500/50 hover:bg-rose-500/10"
              } disabled:opacity-50`}
              title="This council was not helpful"
            >
              👎 {data.scores.filter((s) => s.score === -1).length || ""}
            </button>
            <input
              type="text"
              placeholder="comment (optional)"
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              className="bg-zinc-900 border border-zinc-700 rounded px-2 py-1 text-sm w-56"
            />
          </div>
        </div>
        {scoreError && <div className="text-xs text-rose-300 mt-1">{scoreError}</div>}
      </div>

      {data.outcome && (
        <Card className="p-4 space-y-2">
          <div className="text-zinc-400 text-xs uppercase tracking-wider">Outcome</div>
          <div className="flex flex-wrap gap-x-6 gap-y-1 text-sm font-mono">
            <div>
              <span className="text-zinc-500">duration </span>
              {fmtDur(data.outcome.total_duration_s ?? 0)}
            </div>
            <div>
              <span className="text-zinc-500">rounds </span>
              {data.outcome.rounds ?? "—"}
              {data.outcome.deliberation ? "" : " (R1 only)"}
              {data.outcome.critique ? " · critique" : ""}
            </div>
            {data.outcome.agreement_score != null && (
              <div>
                <span className="text-zinc-500">agreement </span>
                <span
                  className={
                    data.outcome.agreement_score >= 4
                      ? "text-emerald-300"
                      : data.outcome.agreement_score >= 3
                      ? "text-amber-300"
                      : "text-rose-300"
                  }
                >
                  {data.outcome.agreement_score.toFixed(1)}/5
                </span>
              </div>
            )}
          </div>
          {data.outcome.agreement_reason && (
            <div className="text-sm text-zinc-300">
              <span className="text-zinc-500">reason: </span>
              {data.outcome.agreement_reason}
            </div>
          )}
          {data.outcome.claude_opinion && (
            <details className="text-sm">
              <summary className="cursor-pointer text-zinc-400 hover:text-zinc-200">
                Claude opinion
              </summary>
              <pre className="mt-2 whitespace-pre-wrap font-mono text-zinc-200 max-h-64 overflow-auto">
                {data.outcome.claude_opinion}
              </pre>
            </details>
          )}
          {data.outcome.progress_log && data.outcome.progress_log.length > 0 && (
            <details className="text-sm">
              <summary className="cursor-pointer text-zinc-400 hover:text-zinc-200">
                Progress log ({data.outcome.progress_log.length})
              </summary>
              <ul className="mt-2 font-mono text-xs text-zinc-300 space-y-0.5 max-h-64 overflow-auto">
                {data.outcome.progress_log.map((line, i) => (
                  <li key={i}>{line}</li>
                ))}
              </ul>
            </details>
          )}
        </Card>
      )}

      <AgentRatingsPanel cid={data.council_id} />

      {data.answers.length > 0 && (
        <Card>
          <div className="px-4 py-3 border-b border-zinc-800 text-sm font-medium flex items-baseline justify-between">
            <span>Side-by-side answers</span>
            <span className="text-zinc-500 text-xs">R1 on top · R2 below · scroll horizontally if needed</span>
          </div>
          <div className="p-3 overflow-x-auto">
            <div className="flex gap-3 min-w-max">
              {data.answers.map((a) => (
                <AnswerColumn key={a.agent} a={a} />
              ))}
            </div>
          </div>
        </Card>
      )}

      <Card>
        <div className="px-4 py-3 border-b border-zinc-800 text-sm font-medium flex items-baseline justify-between">
          <span>Agreement matrix</span>
          <span className="text-zinc-500 text-xs">pairwise agreement, 1 (red) → 5 (green)</span>
        </div>
        <AgreementMatrix cid={data.council_id} />
      </Card>

      <Card>
        <div className="px-4 py-3 border-b border-zinc-800 text-sm font-medium flex items-baseline justify-between">
          <span>Vote-flip timeline</span>
          <span className="text-zinc-500 text-xs">how each agent shifted between R1 and R2</span>
        </div>
        <VoteFlipTimeline cid={data.council_id} />
      </Card>

      <Card>
        <div className="px-4 py-3 border-b border-zinc-800 text-sm font-medium flex items-baseline justify-between">
          <span>Trace</span>
          <span className="text-zinc-500 text-xs">Gantt of calls; ticks = skills/tools invoked</span>
        </div>
        <SpanTree cid={data.council_id} />
      </Card>

      {data.rounds.length > 0 && (
        <Card className="p-4">
          <div className="text-zinc-400 text-xs uppercase tracking-wider mb-2">Round summary</div>
          <div className="space-y-1 font-mono text-sm">
            {data.rounds.map((r) => (
              <div key={r.round}>
                <span className="text-zinc-500">r{r.round}:</span>{" "}
                fastest <span className="text-emerald-300">{r.fastest}</span>{" "}
                · slowest <span className="text-rose-300">{r.slowest}</span>{" "}
                · spread {fmtDur(r.spread_s ?? 0)}
              </div>
            ))}
          </div>
        </Card>
      )}

      {[
        { title: "Round 1", rows: r1 },
        { title: "Round 2 (deliberation)", rows: r2 },
      ].map(
        (sec) =>
          sec.rows.length > 0 && (
            <Card key={sec.title}>
              <div className="px-4 py-3 border-b border-zinc-800 text-sm font-medium">
                {sec.title} <span className="text-zinc-500">({sec.rows.length})</span>
              </div>
              <table className="w-full text-sm">
                <thead className="text-zinc-400 text-xs uppercase tracking-wider">
                  <tr>
                    <th className="text-left px-4 py-2">Agent</th>
                    <th className="text-left px-4 py-2">Status</th>
                    <th className="text-right px-4 py-2">Duration</th>
                    <th className="text-right px-4 py-2">Output</th>
                    <th className="text-left px-4 py-2">Started</th>
                    <th className="text-left px-4 py-2">Task</th>
                  </tr>
                </thead>
                <tbody>
                  {sec.rows.map((c) => (
                    <tr key={c.task_id} className="border-t border-zinc-800/60 hover:bg-zinc-900/40">
                      <td className="px-4 py-2">
                        <AgentBadge agent={c.agent} round={c.round} />
                      </td>
                      <td className="px-4 py-2">
                        <StatusBadge status={c.status} />
                      </td>
                      <td className="px-4 py-2 text-right font-mono">{fmtDur(c.duration_s)}</td>
                      <td className="px-4 py-2 text-right font-mono text-zinc-400">
                        {c.output_chars ?? "—"}
                      </td>
                      <td className="px-4 py-2 text-zinc-400">{fmtTs(c.ts)}</td>
                      <td className="px-4 py-2">
                        <Link
                          to={`/calls/${c.task_id}`}
                          className="font-mono text-emerald-300 hover:underline"
                        >
                          {c.task_id}
                        </Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Card>
          )
      )}
    </div>
  );
}

function AnswerColumn({ a }: { a: AgentAnswers }) {
  return (
    <div className="w-80 max-w-md flex-shrink-0 border border-zinc-800 rounded-lg bg-zinc-900/50 flex flex-col">
      <div className="px-3 py-2 border-b border-zinc-800 flex items-center gap-2">
        <AgentBadge agent={a.agent} />
        {a.r1 && <StatusBadge status={a.r1.status} />}
        <span className="ml-auto text-xs text-zinc-500 font-mono">
          R1 {fmtDur(a.r1?.duration_s)}
          {a.r2 && ` · R2 ${fmtDur(a.r2.duration_s)}`}
        </span>
      </div>
      <AnswerPanel title="Round 1" pane={a.r1} />
      {a.r2 ? (
        <>
          <ShiftStrip pane={a.r2} />
          <AnswerPanel title="Round 2" pane={a.r2} />
        </>
      ) : (
        <div className="px-3 py-3 text-xs text-zinc-500 italic border-t border-zinc-800/60">
          No Round 2 (auto-skipped or single round)
        </div>
      )}
    </div>
  );
}

function AnswerPanel({ title, pane }: { title: string; pane: AnswerPaneT | null }) {
  if (!pane) {
    return (
      <div className="px-3 py-3 text-xs text-zinc-500 border-t border-zinc-800/60">{title}: no data</div>
    );
  }
  const text = pane.result_text ?? pane.error ?? "(empty)";
  return (
    <div className="border-t border-zinc-800/60">
      <div className="px-3 py-1.5 flex items-center justify-between">
        <span className="text-xs text-zinc-400 uppercase tracking-wider">{title}</span>
        <button
          onClick={() => navigator.clipboard?.writeText(text)}
          className="text-xs text-zinc-500 hover:text-zinc-200"
          title="Copy to clipboard"
        >
          copy
        </button>
      </div>
      <pre className="px-3 pb-3 text-xs whitespace-pre-wrap font-mono text-zinc-200 max-h-72 overflow-auto">
        {text}
      </pre>
    </div>
  );
}

function ShiftStrip({ pane }: { pane: AnswerPaneT }) {
  if (!pane.position_label) return null;
  const { position_label: lbl, position_delta: delta } = pane;
  const cls =
    lbl === "major"
      ? "bg-rose-500/15 text-rose-200 border-rose-500/40"
      : lbl === "minor"
      ? "bg-amber-500/15 text-amber-200 border-amber-500/40"
      : "bg-emerald-500/15 text-emerald-200 border-emerald-500/40";
  return (
    <div className={`px-3 py-1.5 border-t border-b text-xs flex items-center gap-2 ${cls}`}>
      <span className="font-medium uppercase tracking-wider">R1 → R2 shift: {lbl}</span>
      {delta != null && <span className="font-mono opacity-80">Δ {(delta * 100).toFixed(0)}%</span>}
    </div>
  );
}
