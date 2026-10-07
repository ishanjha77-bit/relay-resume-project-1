package dev.relay.incident;

import java.time.Instant;
import java.util.Map;

/** One alert from any source, normalized. */
public record AlertItem(
        String fingerprint,
        String name,
        String service,
        Severity severity,
        boolean firing,
        String summary,
        Instant startsAt,
        Instant endsAt,
        Map<String, String> labels) {

    /**
     * One firing episode of an alert: the same fingerprint that resolves and
     * fires again starts a new episode. Without a start time there's no telling
     * episodes apart, so the key is null and never matches.
     */
    public static String instanceKey(String fingerprint, Instant startsAt) {
        return startsAt == null ? null : fingerprint + "@" + startsAt.toEpochMilli();
    }
}
