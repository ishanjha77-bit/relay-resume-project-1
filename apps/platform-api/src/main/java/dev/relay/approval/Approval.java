package dev.relay.approval;

import java.time.Instant;
import java.util.Map;
import java.util.UUID;

/**
 * An action an agent asked to take, and what became of it.
 *
 * @param action       the exact JSON text that will run; the approval token binds its SHA-256
 * @param diff         what the action changes, as the approver reads it
 * @param rationale    why the agent wants it: its top hypothesis, in its own words
 * @param result       what the action produced (for a pull request: its number and URL)
 */
public record Approval(
        UUID id,
        UUID incidentId,
        String runId,
        String kind,
        String title,
        String risk,
        String rationale,
        String diff,
        String action,
        String actionSha256,
        String requestedByAgent,
        Instant requestedAt,
        ApprovalStatus status,
        String decidedBy,
        String decisionReason,
        Instant decidedAt,
        Map<String, Object> result,
        String error) {
}
