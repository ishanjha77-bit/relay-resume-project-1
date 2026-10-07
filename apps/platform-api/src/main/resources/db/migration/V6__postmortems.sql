-- Blameless postmortems, drafted by the agent service's postmortem writer when
-- someone resolves an incident. One per incident; a rewrite replaces it. Each
-- is also indexed, section by section, into knowledge_chunks (kind 'postmortem')
-- so the next investigation can find it.
CREATE TABLE postmortems (
    incident_id  uuid         PRIMARY KEY REFERENCES incidents (id) ON DELETE CASCADE,
    run_id       text         NOT NULL,
    title        text         NOT NULL,
    document     jsonb        NOT NULL,  -- the structured postmortem (summary, timeline, action items, ...)
    markdown     text         NOT NULL,  -- the same, rendered; what the console exports
    model        text         NOT NULL,
    written_at   timestamptz  NOT NULL
);
