package dev.relay.incident;

import java.time.Instant;

public record IncidentAlert(
        String fingerprint,
        String name,
        String service,
        String severity,
        String status,
        String summary,
        Instant startsAt,
        Instant endsAt) {
}
