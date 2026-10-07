package dev.relay.incident;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.UUID;

/** One row of the incident inbox. */
public record IncidentSummary(
        UUID id,
        String key,
        String title,
        String service,
        Severity severity,
        IncidentStatus status,
        Instant openedAt,
        Instant resolvedAt,
        String rootCauseCategory,
        String rootCauseService,
        BigDecimal costUsd,
        Instant updatedAt) {

    public static IncidentSummary from(Incident i) {
        return new IncidentSummary(i.id(), i.key(), i.title(), i.service(), i.severity(), i.status(), i.openedAt(),
                i.resolvedAt(), i.rootCauseCategory(), i.rootCauseService(), i.costUsd(), i.updatedAt());
    }
}
