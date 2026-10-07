-- platform-api owns these tables (docs/adr/0002). agent-service never writes
-- them: it publishes events that platform-api persists.

CREATE TABLE incidents (
    id                  uuid           PRIMARY KEY,
    number              bigint         GENERATED ALWAYS AS IDENTITY UNIQUE,
    title               text           NOT NULL,
    service             text,
    severity            text           NOT NULL CHECK (severity IN ('critical', 'warning', 'info')),
    status              text           NOT NULL CHECK (status IN ('OPEN', 'INVESTIGATING', 'DIAGNOSED',
                                                                  'AWAITING_APPROVAL', 'RESOLVED', 'FAILED')),
    source              text           NOT NULL,
    group_key           text           NOT NULL,
    namespace           text           NOT NULL,
    opened_at           timestamptz    NOT NULL,
    resolved_at         timestamptz,
    run_id              text,
    summary             text,
    root_cause_category text,
    root_cause_service  text,
    cost_usd            numeric(12, 6) NOT NULL DEFAULT 0,
    updated_at          timestamptz    NOT NULL DEFAULT now()
);

-- At most one unresolved incident per alert group: Alertmanager retries and
-- repeat notifications attach to it instead of opening duplicates. A FAILED
-- investigation leaves the incident open for a human, so it counts too.
CREATE UNIQUE INDEX incidents_active_group_idx ON incidents (group_key)
    WHERE status <> 'RESOLVED';
CREATE INDEX incidents_opened_idx ON incidents (opened_at DESC, id DESC);

CREATE TABLE incident_alerts (
    incident_id  uuid        NOT NULL REFERENCES incidents (id) ON DELETE CASCADE,
    fingerprint  text        NOT NULL,
    name         text        NOT NULL,
    service      text,
    severity     text,
    status       text        NOT NULL CHECK (status IN ('firing', 'resolved')),
    summary      text,
    starts_at    timestamptz,
    ends_at      timestamptz,
    labels       jsonb       NOT NULL DEFAULT '{}',
    updated_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (incident_id, fingerprint)
);

-- The incident timeline: alerts, status changes, agent milestones, human actions.
CREATE TABLE incident_events (
    id           bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    incident_id  uuid        NOT NULL REFERENCES incidents (id) ON DELETE CASCADE,
    at           timestamptz NOT NULL DEFAULT now(),
    kind         text        NOT NULL,
    message      text        NOT NULL,
    data         jsonb       NOT NULL DEFAULT '{}'
);
CREATE INDEX incident_events_incident_idx ON incident_events (incident_id, at);

-- Every agent step, replayable from the incident page.
CREATE TABLE agent_steps (
    id                 bigint         GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    incident_id        uuid           NOT NULL REFERENCES incidents (id) ON DELETE CASCADE,
    run_id             text           NOT NULL,
    seq                int            NOT NULL,
    agent              text           NOT NULL,
    kind               text           NOT NULL,
    tool               text,
    evidence_id        text,
    input_json         jsonb,
    output_json        jsonb,
    tokens_in          int,
    tokens_out         int,
    cache_read_tokens  int,
    cost_usd           numeric(12, 6),
    latency_ms         int,
    created_at         timestamptz    NOT NULL,
    UNIQUE (run_id, seq)  -- events arrive at least once; a redelivery is a no-op
);
CREATE INDEX agent_steps_incident_idx ON agent_steps (incident_id, run_id, seq);

CREATE TABLE hypotheses (
    id             bigint        GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    incident_id    uuid          NOT NULL REFERENCES incidents (id) ON DELETE CASCADE,
    run_id         text          NOT NULL,
    rank           int           NOT NULL,
    category       text          NOT NULL,
    service        text          NOT NULL,
    component      text,
    summary        text          NOT NULL,
    confidence     numeric(4, 3) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    evidence_json  jsonb         NOT NULL,
    suggested_fix  text,
    verdict        text          NOT NULL,
    created_at     timestamptz   NOT NULL DEFAULT now(),
    UNIQUE (run_id, rank)
);

-- Transactional outbox: events are written in the same transaction as the
-- state change and published to Redis after commit, so a crash can't commit
-- an incident without its event (or publish an event for a rolled-back one).
CREATE TABLE outbox (
    id            bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    stream        text        NOT NULL,
    payload       jsonb       NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    published_at  timestamptz
);
CREATE INDEX outbox_pending_idx ON outbox (id) WHERE published_at IS NULL;

CREATE TABLE users (
    username       text   PRIMARY KEY,
    display_name   text   NOT NULL,
    password_hash  text   NOT NULL,
    roles          text[] NOT NULL
);
