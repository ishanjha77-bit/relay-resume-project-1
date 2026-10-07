-- The learning loop: responders mark a hypothesis as the root cause (+1) or not
-- (-1). On real incidents, where no eval knows the answer, these votes are the
-- labels that accuracy and calibration can be measured against. A hypothesis is
-- identified by its run, category and service: ranks move when the reviewer
-- re-ranks.
CREATE TABLE hypothesis_votes (
    incident_id uuid        NOT NULL REFERENCES incidents (id) ON DELETE CASCADE,
    run_id      text        NOT NULL,
    category    text        NOT NULL,
    service     text        NOT NULL,
    username    text        NOT NULL,
    vote        smallint    NOT NULL CHECK (vote IN (-1, 1)),
    voted_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (incident_id, run_id, category, service, username)
);
