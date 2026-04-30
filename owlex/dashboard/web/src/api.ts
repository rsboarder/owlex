export type Call = {
  task_id: string;
  ts: string;
  started_at: string;
  completed_at: string | null;
  agent: string;
  round: number;
  command: string;
  status: string;
  duration_s: number | null;
  council_id: string | null;
  error: string | null;
  output_chars: number | null;
  elapsed_s?: number;
};

export type AgentStat = {
  agent: string;
  total: number;
  completed: number;
  failed: number;
  success_rate: number;
  avg_s: number;
  p50_s: number;
  p95_s: number;
};

export type Stats = {
  total: number;
  completed: number;
  failed: number;
  in_flight: number;
  by_agent: AgentStat[];
};

export type Session = {
  council_id: string;
  started_at: string;
  ended_at: string;
  calls: number;
  failed: number;
  running: number;
  agents: string[];
  agreement_score: number | null;
  deliberation: boolean | null;
};

export type Outcome = {
  completed_at: string;
  total_duration_s: number | null;
  agreement_score: number | null;
  agreement_reason: string | null;
  progress_log: string[];
  claude_opinion: string | null;
  deliberation: boolean;
  critique: boolean;
  rounds: number | null;
};

export type Round = {
  round: number;
  ts: string;
  fastest: string | null;
  slowest: string | null;
  spread_s: number | null;
  agent_order: string[];
};

export type SessionScore = {
  score: -1 | 1;
  label: string | null;
  comment: string | null;
  rater: string;
  ts: string;
};

export type AnswerPane = {
  task_id: string;
  status: string;
  duration_s: number | null;
  started_at: string;
  result_text: string | null;
  error: string | null;
  position_delta?: number;
  position_label?: "unchanged" | "minor" | "major";
};

export type AgentAnswers = {
  agent: string;
  r1: AnswerPane | null;
  r2: AnswerPane | null;
};

export type SessionDetail = {
  council_id: string;
  calls: Call[];
  rounds: Round[];
  outcome: Outcome | null;
  scores: SessionScore[];
  answers: AgentAnswers[];
};

export type Skill = {
  seq: number;
  ts: string | null;
  kind: "skill" | "tool";
  name: string;
  args_summary: string | null;
};

export type CallDetail = Call & {
  prompt_text: string | null;
  result_text: string | null;
  last_lines: string[];
  session_id: string | null;
  legacy: boolean;
  skills: Skill[];
};

async function get<T>(path: string): Promise<T> {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

export const api = {
  stats: (since?: string) => get<Stats>(`/api/stats${since ? `?since=${since}` : ""}`),
  sessions: (
    params: { limit?: number; offset?: number; since?: string; until?: string; agreement_min?: number; agreement_max?: number } = {},
  ) => {
    const qs = new URLSearchParams();
    qs.set("limit", String(params.limit ?? 50));
    qs.set("offset", String(params.offset ?? 0));
    if (params.since) qs.set("since", params.since);
    if (params.until) qs.set("until", params.until);
    if (params.agreement_min != null) qs.set("agreement_min", String(params.agreement_min));
    if (params.agreement_max != null) qs.set("agreement_max", String(params.agreement_max));
    return get<{ sessions: Session[] }>(`/api/sessions?${qs}`);
  },
  session: (cid: string) => get<SessionDetail>(`/api/sessions/${cid}`),
  calls: (
    params: {
      agent?: string;
      status?: string;
      q?: string;
      sort?: string;
      duration_min?: number;
      duration_max?: number;
      hour?: number;
      tool?: string;
      skill?: string;
      limit?: number;
      offset?: number;
    } = {},
  ) => {
    const qs = new URLSearchParams();
    if (params.agent) qs.set("agent", params.agent);
    if (params.status) qs.set("status", params.status);
    if (params.q) qs.set("q", params.q);
    if (params.sort) qs.set("sort", params.sort);
    if (params.duration_min != null) qs.set("duration_min", String(params.duration_min));
    if (params.duration_max != null) qs.set("duration_max", String(params.duration_max));
    if (params.hour != null) qs.set("hour", String(params.hour));
    if (params.tool) qs.set("tool", params.tool);
    if (params.skill) qs.set("skill", params.skill);
    qs.set("limit", String(params.limit ?? 100));
    qs.set("offset", String(params.offset ?? 0));
    return get<{ calls: Call[] }>(`/api/calls?${qs}`);
  },
  skillNames: (agent?: string) =>
    get<{
      tools: Array<{ name: string; count: number }>;
      skills: Array<{ name: string; count: number }>;
    }>(`/api/skills/names${agent ? `?agent=${encodeURIComponent(agent)}` : ""}`),
  call: (id: string) => get<CallDetail>(`/api/calls/${id}`),
  inFlight: () => get<{ calls: Call[] }>(`/api/calls/in_flight`),
  timeseries: (days = 30) => get<{ days: Array<Record<string, any>> }>(`/api/timeseries?days=${days}`),
  latencyDist: () =>
    get<{
      by_agent: Array<{
        agent: string; n: number;
        min: number; p25: number; p50: number; p75: number; p95: number; max: number;
      }>;
    }>(`/api/latency_distribution`),
  agreementDist: () =>
    get<{
      buckets: Array<{ bin: number; live: number; backfilled: number; count: number }>;
      total: number;
      live: number;
      backfilled: number;
    }>(`/api/agreement_distribution`),
  leaderboard: (since?: string) =>
    get<{
      agents: Array<{
        rank: number;
        agent: string;
        total: number;
        completed: number;
        failed: number;
        success_pct: number;
        ci_low_pct: number;
        ci_high_pct: number;
        p50_s: number;
        p95_s: number;
        agreement_score: number | null;
        blind_rating_avg: number | null;
        blind_rating_n: number;
        spark: Array<{ day: string; calls: number }>;
      }>;
    }>(`/api/leaderboard${since ? `?since=${encodeURIComponent(since)}` : ""}`),
  scoreSession: (cid: string, score: 1 | -1, comment?: string, label?: string) =>
    post<{ ok: true }>(`/api/sessions/${cid}/score`, { score, comment, label }),
  matrix: (cid: string) =>
    get<{
      participants: string[];
      cells: Array<Array<number | null>>;
      sources: Array<Array<string | null>>;
      reasons: Array<Array<string | null>>;
    }>(`/api/sessions/${cid}/matrix`),
  tree: (cid: string) =>
    get<{
      council_id: string;
      started_at: string | null;
      ended_at: string | null;
      rounds: Array<{
        round: number;
        calls: Array<{
          task_id: string;
          agent: string;
          status: string;
          started_at: string;
          completed_at: string | null;
          duration_s: number | null;
          skills: Array<{ seq: number; ts: string | null; kind: string; name: string; args_summary: string | null }>;
        }>;
      }>;
    }>(`/api/sessions/${cid}/tree`),
  timeline: (cid: string) =>
    get<{
      council_id: string;
      agents: Array<{
        agent: string;
        r1: { task_id: string; started_at: string; completed_at: string | null; duration_s: number | null; status: string } | null;
        r2: {
          task_id: string;
          started_at: string;
          completed_at: string | null;
          duration_s: number | null;
          status: string;
          position_delta?: number;
          position_label?: "unchanged" | "minor" | "major";
        } | null;
      }>;
    }>(`/api/sessions/${cid}/timeline`),
  latencyHeatmap: (agent?: string, days = 30) =>
    get<{
      hours: number[];
      buckets: Array<{ label: string; lo: number; hi: number }>;
      grid: number[][];
      total: number;
    }>(
      `/api/latency_heatmap?days=${days}${agent ? `&agent=${encodeURIComponent(agent)}` : ""}`,
    ),
  scoresTimeseries: (days = 30, rater?: string) => {
    const qs = new URLSearchParams();
    qs.set("days", String(days));
    if (rater) qs.set("rater", rater);
    return get<{ days: Array<{ day: string; up: number; down: number }> }>(
      `/api/scores/timeseries?${qs}`,
    );
  },
  agentScores: (cid: string) =>
    get<{
      council_id: string;
      ratings: Array<{
        agent: string;
        rater: string;
        score: -1 | 1;
        dimensions: Record<string, number> | null;
        reason: string | null;
        ts: string;
      }>;
    }>(`/api/sessions/${cid}/agent_scores`),
  leaderboardByModel: () =>
    get<{
      rows: Array<{
        agent: string;
        model: string;
        rated_n: number;
        blind_avg: number | null;
        avg_duration_s: number;
      }>;
    }>(`/api/leaderboard/by_model`),
  blindIntegrity: () =>
    get<{
      agents: Array<{
        agent: string;
        blind_avg: number;
        blind_n: number;
        agreement_norm: number | null;
        deviation: number | null;
      }>;
    }>(`/api/integrity/blind_vs_agreement`),
};
