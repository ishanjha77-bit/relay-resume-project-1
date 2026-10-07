-- One row per scenario per eval batch (a pass of evals/runner.py over the
-- scenarios). Written by the runner, read by the console's evals dashboard.
CREATE TABLE eval_runs (
    id                 uuid           PRIMARY KEY,
    batch              text           NOT NULL,
    scenario           text           NOT NULL,
    category           text           NOT NULL,  -- the expected root-cause category
    -- scored: the agent gave a verdict; agent_failed: it gave none (counts as wrong);
    -- no_alert / timeout: the sandbox never alerted or Relay never answered in time.
    status             text           NOT NULL CHECK (status IN ('scored', 'agent_failed', 'no_alert', 'timeout')),
    correct            boolean,
    correct_top3       boolean,
    top_category       text,
    top_service        text,
    confidence         numeric(4, 3),
    fix_score          numeric(4, 3),            -- share of the expected fix keywords in the suggested fix
    citations_verified numeric(4, 3),            -- share of quotes found verbatim in their evidence
    injection_ok       boolean,                  -- flagged an injection exactly when there was one
    steps              integer,                  -- tool calls
    llm_calls          integer,
    prompt_tokens      integer,
    output_tokens      integer,
    cache_read_tokens  integer,
    cost_usd           numeric(12, 6),
    seconds            numeric(8, 1),            -- fault injected -> verdict
    agent_seconds      numeric(8, 1),            -- incident opened -> verdict
    model              text,
    incident_id        uuid,
    run_id             text,
    run_at             timestamptz    NOT NULL,
    details            jsonb          NOT NULL DEFAULT '{}',
    UNIQUE (batch, scenario)
);

CREATE INDEX eval_runs_run_at_idx ON eval_runs (run_at DESC);
