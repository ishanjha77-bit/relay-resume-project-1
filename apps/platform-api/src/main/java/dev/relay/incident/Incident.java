package dev.relay.incident;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.UUID;

public record Incident(
        UUID id,
        long number,
        String title,
        String service,
        Severity severity,
        IncidentStatus status,
        String source,
        String groupKey,
        String namespace,
        Instant openedAt,
        Instant resolvedAt,
        String runId,
        String summary,
        String rootCauseCategory,
        String rootCauseService,
        BigDecimal costUsd,
        Instant updatedAt) {

    public String key() {
        return "INC-" + number;
    }
}
