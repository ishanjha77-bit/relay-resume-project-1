-- Human in the loop: an agent asks to act, someone with the APPROVER role decides,
-- and only then can the action run. Rows are the audit trail: a decision is final
-- (PENDING -> APPROVED | REJECTED), and an approved action records its outcome
-- (APPROVED -> EXECUTED | FAILED).
--
-- `action` is the exact JSON text that will be executed. The approval token binds
-- its SHA-256, so it is stored as text, byte for byte, never as jsonb.
CREATE TABLE approvals (
    id                  uuid         PRIMARY KEY,  -- chosen by the agent, so a redelivered request is a no-op
    incident_id         uuid         NOT NULL REFERENCES incidents (id),
    run_id              text         NOT NULL,
    kind                text         NOT NULL,
    title               text         NOT NULL,
    risk                text         NOT NULL CHECK (risk IN ('low', 'medium', 'high')),
    rationale           text         NOT NULL,
    diff                text         NOT NULL,
    action              text         NOT NULL,
    action_sha256       text         NOT NULL,
    requested_by_agent  text         NOT NULL,
    requested_at        timestamptz  NOT NULL,
    status              text         NOT NULL DEFAULT 'PENDING'
                        CHECK (status IN ('PENDING', 'APPROVED', 'REJECTED', 'EXECUTED', 'FAILED')),
    decided_by          text,
    decision_reason     text,
    decided_at          timestamptz,
    result              jsonb,
    error               text,
    updated_at          timestamptz  NOT NULL DEFAULT now()
);

CREATE INDEX approvals_incident_idx ON approvals (incident_id, requested_at DESC);
