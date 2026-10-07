package dev.relay.sandbox.inventory.faults;

import java.util.Map;
import java.util.Optional;
import java.util.concurrent.ThreadLocalRandom;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import tools.jackson.core.type.TypeReference;
import tools.jackson.databind.json.JsonMapper;

/**
 * Runtime fault flags for this service, polled from Redis key {@code ops:flags:<service>}.
 * Polling is silent on purpose: fault injection must not leave log lines the agent
 * could read instead of diagnosing the real symptoms. See sandbox/chaos/README.md.
 */
@Component
public class FaultFlags {

    private static final Logger log = LoggerFactory.getLogger(FaultFlags.class);
    private static final TypeReference<Map<String, Map<String, Object>>> FLAGS = new TypeReference<>() {
    };

    private final StringRedisTemplate redis;
    private final JsonMapper json;
    private final String key;
    private volatile Map<String, Map<String, Object>> active = Map.of();

    FaultFlags(StringRedisTemplate redis, JsonMapper json, @Value("${spring.application.name}") String service) {
        this.redis = redis;
        this.json = json;
        this.key = "ops:flags:" + service;
    }

    @Scheduled(fixedDelay = 2000, initialDelay = 1000)
    void refresh() {
        try {
            String raw = redis.opsForValue().get(key);
            active = raw == null ? Map.of() : json.readValue(raw, FLAGS);
        } catch (RuntimeException e) {
            // Keep the last known flags; Redis being down must not change behaviour.
            log.debug("flag refresh failed: {}", e.getMessage());
        }
    }

    public Optional<Params> active(String name) {
        return Optional.ofNullable(active.get(name)).map(Params::new);
    }

    /** The flag is set and a random draw falls under its "ratio" param (default 1.0). */
    public Optional<Params> hit(String name) {
        return active(name).filter(p -> ThreadLocalRandom.current().nextDouble() < p.number("ratio", 1.0));
    }

    public record Params(Map<String, Object> values) {

        public double number(String key, double fallback) {
            Object v = values.get(key);
            if (v instanceof Number n) {
                return n.doubleValue();
            }
            if (v instanceof String s) {
                try {
                    return Double.parseDouble(s);
                } catch (NumberFormatException ignored) {
                    return fallback;
                }
            }
            return fallback;
        }

        public int integer(String key, int fallback) {
            return (int) number(key, fallback);
        }
    }
}
