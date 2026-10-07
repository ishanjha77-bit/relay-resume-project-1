-- The reviewer agent's verdict on each hypothesis. It can lower confidence and
-- re-rank; original_confidence keeps what the investigator stated. Both are NULL
-- for a hypothesis that was never reviewed.
ALTER TABLE hypotheses ADD COLUMN original_confidence numeric(4, 3) CHECK (original_confidence BETWEEN 0 AND 1);
ALTER TABLE hypotheses ADD COLUMN review jsonb;  -- {"verdict": "supported" | "weak" | "unsupported", "reason": ..., "model": ...}
