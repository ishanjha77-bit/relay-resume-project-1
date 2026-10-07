package dev.relay.incident;

import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.Base64;
import java.util.UUID;

/**
 * Opaque keyset cursor for the incident list: the (opened_at, id) of the last
 * row seen. Stable under inserts, unlike OFFSET pagination.
 */
public record Cursor(Instant openedAt, UUID id) {

    public String encode() {
        String raw = openedAt.getEpochSecond() + "." + openedAt.getNano() + ":" + id;
        return Base64.getUrlEncoder().withoutPadding().encodeToString(raw.getBytes(StandardCharsets.UTF_8));
    }

    public static Cursor decode(String token) {
        try {
            String raw = new String(Base64.getUrlDecoder().decode(token), StandardCharsets.UTF_8);
            int colon = raw.indexOf(':');
            String[] time = raw.substring(0, colon).split("\\.");
            return new Cursor(Instant.ofEpochSecond(Long.parseLong(time[0]), Long.parseLong(time[1])),
                    UUID.fromString(raw.substring(colon + 1)));
        } catch (RuntimeException e) {
            throw new IllegalArgumentException("invalid cursor");
        }
    }

    public static Cursor after(Incident incident) {
        return new Cursor(incident.openedAt(), incident.id());
    }
}
