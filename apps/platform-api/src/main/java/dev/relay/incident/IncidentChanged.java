package dev.relay.incident;

import java.util.UUID;

/** Published inside a transaction; live updates go out after it commits. */
public record IncidentChanged(UUID incidentId) {
}
