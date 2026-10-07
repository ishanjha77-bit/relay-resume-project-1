package dev.relay.postmortem;

import java.time.Instant;
import java.util.UUID;

import com.fasterxml.jackson.annotation.JsonRawValue;

/**
 * A blameless postmortem, as the agent service's postmortem writer drafted it.
 *
 * @param document the structured postmortem (contracts/schemas/agent-event.schema.json, postmortemWritten)
 * @param markdown the same, rendered as Markdown
 */
public record Postmortem(
        UUID incidentId,
        String runId,
        String title,
        @JsonRawValue String document,
        String markdown,
        String model,
        Instant writtenAt) {
}
