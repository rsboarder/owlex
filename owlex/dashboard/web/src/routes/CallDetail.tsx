import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import {
  AgentBadge,
  Card,
  ErrorBox,
  Loading,
  StatusBadge,
  fmtDur,
  fmtTs,
  useFetch,
} from "../components";

export default function CallDetail() {
  const { tid = "" } = useParams();
  const { data, err, loading } = useFetch(() => api.call(tid), [tid]);
  if (loading) return <Loading />;
  if (err) return <ErrorBox err={err} />;
  if (!data) return null;

  const skillCount = data.skills.filter((s) => s.kind === "skill").length;
  const toolCount = data.skills.filter((s) => s.kind === "tool").length;

  return (
    <div className="space-y-6">
      <div>
        <Link to="/calls" className="text-zinc-400 hover:text-white text-sm">
          ← all calls
        </Link>
        <div className="flex items-center gap-3 mt-1">
          <h1 className="text-2xl font-mono">{data.task_id}</h1>
          <AgentBadge agent={data.agent} round={data.round} />
          <StatusBadge status={data.status} />
        </div>
      </div>

      {data.legacy && (
        <Card className="p-3 border-amber-500/40 bg-amber-500/10">
          <div className="text-amber-200 text-sm">
            ⚠️ Legacy call — migrated from <code className="font-mono text-xs">timing.jsonl</code>. Only the first 500
            chars of the response were ever persisted; the original full output ({data.output_chars?.toLocaleString() ?? "?"}{" "}
            chars) is no longer recoverable. Live councils after the schema refactor capture the complete response.
          </div>
        </Card>
      )}

      <Card className="p-4 grid grid-cols-2 sm:grid-cols-4 gap-4 text-sm">
        <Stat label="Duration" value={fmtDur(data.duration_s)} mono />
        <Stat label="Started" value={fmtTs(data.ts)} />
        <Stat
          label="Council"
          value={
            data.council_id ? (
              <Link to={`/sessions/${data.council_id}`} className="text-emerald-300 hover:underline">
                {data.council_id}
              </Link>
            ) : (
              "—"
            )
          }
        />
        <Stat label="Output chars" value={data.output_chars?.toLocaleString() ?? "—"} mono />
      </Card>

      {data.error && (
        <Card className="p-4">
          <div className="text-zinc-400 text-xs uppercase tracking-wider mb-2">Error</div>
          <pre className="text-rose-300 text-sm whitespace-pre-wrap font-mono">{data.error}</pre>
        </Card>
      )}

      <Card>
        <div className="px-4 py-3 border-b border-zinc-800 text-sm font-medium flex items-center gap-3">
          <span>Skills & tools invoked</span>
          <span className="text-zinc-500 text-xs">
            {skillCount} skill{skillCount !== 1 ? "s" : ""} · {toolCount} tool call
            {toolCount !== 1 ? "s" : ""}
          </span>
        </div>
        {data.skills.length === 0 ? (
          <div className="px-4 py-6 text-zinc-500 text-sm">
            No structured invocations found in agent session file.
            {data.agent === "aichat" && " (aichat session format does not expose tool calls.)"}
          </div>
        ) : (
          <ul className="divide-y divide-zinc-800/60">
            {data.skills.map((s) => (
              <li key={s.seq} className="px-4 py-2 flex items-start gap-3 text-sm">
                <span
                  className={`px-1.5 py-0.5 rounded text-xs border self-start ${
                    s.kind === "skill"
                      ? "bg-amber-500/15 text-amber-300 border-amber-500/30"
                      : "bg-sky-500/15 text-sky-300 border-sky-500/30"
                  }`}
                >
                  {s.kind}
                </span>
                <Link
                  to={`/calls?${s.kind}=${encodeURIComponent(s.name)}`}
                  className="font-mono text-zinc-100 hover:text-emerald-300 hover:underline"
                  title={`Filter to all calls that invoked ${s.kind} ${s.name}`}
                >
                  {s.name}
                </Link>
                {s.args_summary && (
                  <span className="font-mono text-zinc-500 truncate flex-1">{s.args_summary}</span>
                )}
                {s.ts && <span className="text-zinc-500 text-xs">{fmtTs(s.ts)}</span>}
              </li>
            ))}
          </ul>
        )}
      </Card>

      {data.prompt_text && (
        <Card>
          <div className="px-4 py-3 border-b border-zinc-800 text-sm font-medium">Prompt</div>
          <pre className="px-4 py-3 text-sm whitespace-pre-wrap font-mono text-zinc-200 max-h-96 overflow-auto">
            {data.prompt_text}
          </pre>
        </Card>
      )}

      {data.result_text && (
        <Card>
          <div className="px-4 py-3 border-b border-zinc-800 text-sm font-medium flex items-center gap-3">
            <span>Response</span>
            <span className="text-zinc-500 text-xs">{data.result_text.length.toLocaleString()} chars</span>
          </div>
          <pre className="px-4 py-3 text-sm whitespace-pre-wrap font-mono text-zinc-200 max-h-[600px] overflow-auto">
            {data.result_text}
          </pre>
        </Card>
      )}

      {data.session_id && (
        <div className="text-xs text-zinc-500 font-mono">session_id: {data.session_id}</div>
      )}

      {data.last_lines.length > 0 && (
        <Card>
          <div className="px-4 py-3 border-b border-zinc-800 text-sm font-medium">Last output lines</div>
          <pre className="px-4 py-3 text-xs whitespace-pre-wrap font-mono text-zinc-300 leading-relaxed">
            {data.last_lines.join("\n")}
          </pre>
        </Card>
      )}
    </div>
  );
}

function Stat({
  label,
  value,
  mono,
}: {
  label: string;
  value: React.ReactNode;
  mono?: boolean;
}) {
  return (
    <div>
      <div className="text-zinc-400 text-xs uppercase tracking-wider">{label}</div>
      <div className={`mt-0.5 ${mono ? "font-mono" : ""}`}>{value}</div>
    </div>
  );
}
