-- The learning loop: responders mark the runbooks and postmortems triage showed
-- them as helpful (+1) or not (-1) for an incident. The knowledge base's search
-- (mcp-servers/runbooks) boosts or lowers a document by its net votes, so what
-- helped last time ranks higher next time. No foreign key: the knowledge base
-- stays self-contained, like knowledge_chunks.
CREATE TABLE document_votes (
    doc_id      text        NOT NULL,
    incident_id uuid        NOT NULL,
    username    text        NOT NULL,
    vote        smallint    NOT NULL CHECK (vote IN (-1, 1)),
    voted_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (doc_id, incident_id, username)
);
