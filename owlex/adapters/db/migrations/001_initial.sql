CREATE TABLE IF NOT EXISTS calls (
    task_id       TEXT PRIMARY KEY,
    agent         TEXT NOT NULL,
    round         INTEGER NOT NULL,
    command       TEXT NOT NULL,
    council_id    TEXT,
    status        TEXT NOT NULL,
    started_at    TEXT NOT NULL,
    completed_at  TEXT,
    duration_s    REAL,
    prompt_text   TEXT,
    result_text   TEXT,
    output_chars  INTEGER,
    error         TEXT,
    last_lines    TEXT,
    session_id    TEXT,
    legacy        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_calls_started_at ON calls(started_at);
CREATE INDEX IF NOT EXISTS idx_calls_agent      ON calls(agent);
CREATE INDEX IF NOT EXISTS idx_calls_council    ON calls(council_id);
CREATE INDEX IF NOT EXISTS idx_calls_status     ON calls(status);

CREATE TABLE IF NOT EXISTS council_rounds (
    council_id  TEXT NOT NULL,
    round       INTEGER NOT NULL,
    ts          TEXT NOT NULL,
    fastest     TEXT,
    slowest     TEXT,
    spread_s    REAL,
    agent_order TEXT,
    PRIMARY KEY (council_id, round)
);

CREATE TABLE IF NOT EXISTS council_outcomes (
    council_id        TEXT PRIMARY KEY,
    completed_at      TEXT NOT NULL,
    total_duration_s  REAL,
    agreement_score   REAL,
    agreement_reason  TEXT,
    progress_log      TEXT,
    claude_opinion    TEXT,
    deliberation      INTEGER NOT NULL DEFAULT 0,
    critique          INTEGER NOT NULL DEFAULT 0,
    rounds            INTEGER
);

CREATE TABLE IF NOT EXISTS skill_invocations (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id      TEXT NOT NULL,
    seq          INTEGER NOT NULL,
    ts           TEXT,
    kind         TEXT NOT NULL,
    name         TEXT NOT NULL,
    args_summary TEXT
);
CREATE INDEX IF NOT EXISTS idx_skill_task ON skill_invocations(task_id);

CREATE TABLE IF NOT EXISTS skill_parse_state (
    task_id    TEXT PRIMARY KEY,
    parsed_at  TEXT NOT NULL,
    found      INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS session_scores (
    council_id TEXT NOT NULL,
    rater      TEXT NOT NULL DEFAULT 'human',
    score      INTEGER NOT NULL,
    label      TEXT,
    comment    TEXT,
    ts         TEXT NOT NULL,
    PRIMARY KEY (council_id, rater, ts)
);
CREATE INDEX IF NOT EXISTS idx_scores_council ON session_scores(council_id);

CREATE TABLE IF NOT EXISTS pairwise_agreements (
    council_id  TEXT NOT NULL,
    agent_a     TEXT NOT NULL,
    agent_b     TEXT NOT NULL,
    score       REAL NOT NULL,
    reason      TEXT,
    source      TEXT NOT NULL,
    computed_at TEXT NOT NULL,
    PRIMARY KEY (council_id, agent_a, agent_b)
);
CREATE INDEX IF NOT EXISTS idx_pa_council ON pairwise_agreements(council_id);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
