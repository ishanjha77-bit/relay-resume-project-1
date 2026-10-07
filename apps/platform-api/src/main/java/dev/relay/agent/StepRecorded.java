package dev.relay.agent;

import java.util.UUID;

/** Published inside the transaction that stored a step; streamed to the console after commit. */
public record StepRecorded(UUID incidentId, AgentStep step) {
}
