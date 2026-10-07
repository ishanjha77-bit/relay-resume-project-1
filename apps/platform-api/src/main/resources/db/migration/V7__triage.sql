-- The triage agent's first look at the incident's latest run: severity, where to
-- start, leads, and the runbooks and postmortems the alerts resemble
-- (contracts/schemas/agent-event.schema.json, triageCompleted, plus run_id).
ALTER TABLE incidents ADD COLUMN triage jsonb;
