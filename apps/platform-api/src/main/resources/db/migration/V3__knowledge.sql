-- Relay's memory: runbooks and postmortems, chunked for retrieval. The runbooks
-- MCP server writes the runbook chunks and fills in every embedding; the
-- platform adds postmortems (embedding NULL until the runbooks server gets to
-- them). Search is hybrid: full-text rank and vector similarity, fused (RRF).
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE knowledge_chunks (
    id          bigserial    PRIMARY KEY,
    doc_id      text         NOT NULL,  -- runbook:<name> or postmortem:<incident key>
    kind        text         NOT NULL CHECK (kind IN ('runbook', 'postmortem')),
    title       text         NOT NULL,
    section     text         NOT NULL,
    position    integer      NOT NULL,  -- order of the section within its document
    text        text         NOT NULL,
    checksum    text         NOT NULL,  -- of title, section and text: a change clears the embedding
    -- BAAI/bge-small-en-v1.5 (384 dimensions, cosine).
    embedding   vector(384),
    tsv         tsvector     GENERATED ALWAYS AS (
                    setweight(to_tsvector('english', title || ' ' || section), 'A')
                    || setweight(to_tsvector('english', text), 'B')) STORED,
    updated_at  timestamptz  NOT NULL DEFAULT now(),
    UNIQUE (doc_id, section)
);

CREATE INDEX knowledge_chunks_tsv_idx ON knowledge_chunks USING gin (tsv);
CREATE INDEX knowledge_chunks_embedding_idx ON knowledge_chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX knowledge_chunks_unembedded_idx ON knowledge_chunks (id) WHERE embedding IS NULL;
