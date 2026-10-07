package dev.relay.agent;

import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.UUID;

/** One agent step, as published by agent-service (contracts/schemas/agent-event.schema.json). */
public record AgentEvent(
        UUID eventId,
        String type,
        String runId,
        UUID incidentId,
        int seq,
        Instant at,
        Map<String, Object> data) {

    public String string(String key) {
        Object value = data.get(key);
        return value == null ? null : value.toString();
    }

    public Integer integer(String key) {
        return data.get(key) instanceof Number n ? n.intValue() : null;
    }

    public Double number(String key) {
        return data.get(key) instanceof Number n ? n.doubleValue() : null;
    }

    @SuppressWarnings("unchecked")
    public Map<String, Object> object(String key) {
        return data.get(key) instanceof Map<?, ?> m ? (Map<String, Object>) m : Map.of();
    }

    @SuppressWarnings("unchecked")
    public static List<Map<String, Object>> objects(Object value) {
        return value instanceof List<?> list ? (List<Map<String, Object>>) list : List.of();
    }
}
