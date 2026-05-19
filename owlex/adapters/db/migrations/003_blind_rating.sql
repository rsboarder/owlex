ALTER TABLE council_outcomes ADD COLUMN backfilled INTEGER NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS agent_scores (
    council_id  TEXT NOT NULL,
    agent       TEXT NOT NULL,
    rater       TEXT NOT NULL,
    score       INTEGER NOT NULL,
    dimensions  TEXT,
    reason      TEXT,
    ts          TEXT NOT NULL,
    PRIMARY KEY (council_id, agent, rater, ts)
);
CREATE INDEX IF NOT EXISTS idx_agent_scores_council ON agent_scores(council_id);
CREATE INDEX IF NOT EXISTS idx_agent_scores_agent   ON agent_scores(agent);

CREATE TABLE IF NOT EXISTS council_anonymization (
    council_id TEXT NOT NULL,
    label      TEXT NOT NULL,
    agent      TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (council_id, label)
);
CREATE INDEX IF NOT EXISTS idx_anon_council ON council_anonymization(council_id);
