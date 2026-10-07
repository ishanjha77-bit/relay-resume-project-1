package dev.relay.agent;

import java.net.InetAddress;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.UUID;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.context.SmartLifecycle;
import org.springframework.data.domain.Range;
import org.springframework.data.redis.RedisSystemException;
import org.springframework.data.redis.connection.RedisConnectionFactory;
import org.springframework.data.redis.connection.stream.Consumer;
import org.springframework.data.redis.connection.stream.MapRecord;
import org.springframework.data.redis.connection.stream.PendingMessage;
import org.springframework.data.redis.connection.stream.ReadOffset;
import org.springframework.data.redis.connection.stream.RecordId;
import org.springframework.data.redis.connection.stream.StreamOffset;
import org.springframework.data.redis.core.RedisCallback;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.stream.StreamMessageListenerContainer;
import org.springframework.data.redis.stream.StreamMessageListenerContainer.StreamMessageListenerContainerOptions;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import dev.relay.RelayProperties;
import tools.jackson.core.type.TypeReference;
import tools.jackson.databind.json.JsonMapper;

/**
 * Consumes the agent-events stream as part of a consumer group, with manual
 * acknowledgement after the event's transaction commits.
 *
 * <ul>
 *   <li>Malformed events are logged and acknowledged — they can never succeed.</li>
 *   <li>Other failures (database down) leave the event pending; the reclaimer
 *       retries it, and also takes over events stranded by a crashed consumer.</li>
 *   <li>After {@value #MAX_DELIVERIES} attempts an event is dead-lettered (logged and acknowledged).</li>
 * </ul>
 */
@Component
class AgentEventsListener implements SmartLifecycle {

    private static final Logger log = LoggerFactory.getLogger(AgentEventsListener.class);
    private static final Duration RECLAIM_IDLE = Duration.ofSeconds(60);
    private static final int MAX_DELIVERIES = 5;
    private static final TypeReference<Map<String, Object>> MAP = new TypeReference<>() {
    };

    private final RedisConnectionFactory connectionFactory;
    private final StringRedisTemplate redis;
    private final AgentEventHandler handler;
    private final JsonMapper json;
    private final String stream;
    private final String group;
    private final String consumer;
    private StreamMessageListenerContainer<String, MapRecord<String, String, String>> container;

    AgentEventsListener(RedisConnectionFactory connectionFactory, StringRedisTemplate redis, AgentEventHandler handler,
            JsonMapper json, RelayProperties properties) {
        this.connectionFactory = connectionFactory;
        this.redis = redis;
        this.handler = handler;
        this.json = json;
        this.stream = properties.streams().agentEvents();
        this.group = properties.streams().consumerGroup();
        this.consumer = consumerName();
    }

    @Override
    public void start() {
        ensureGroup();
        var options = StreamMessageListenerContainerOptions.builder()
                .pollTimeout(Duration.ofSeconds(2))
                .batchSize(50)
                .errorHandler(e -> log.warn("Agent event stream poll failed: {}", e.getMessage()))
                .build();
        container = StreamMessageListenerContainer.create(connectionFactory, options);
        container.receive(Consumer.from(group, consumer), StreamOffset.create(stream, ReadOffset.lastConsumed()),
                record -> process(record.getId(), record.getValue().get("event")));
        container.start();
        log.info("Consuming {} as {}/{}", stream, group, consumer);
    }

    @Override
    public void stop() {
        if (container != null) {
            container.stop();
        }
    }

    @Override
    public boolean isRunning() {
        return container != null && container.isRunning();
    }

    void process(RecordId id, String payload) {
        AgentEvent event;
        String agent;
        try {
            Map<String, Object> raw = json.readValue(payload, MAP);
            event = toEvent(raw);
            agent = raw.getOrDefault("agent", "investigator").toString();
        } catch (RuntimeException e) { // includes Jackson 3's (unchecked) JacksonException
            log.error("Dropping malformed agent event {}: {}", id, e.getMessage());
            ack(id);
            return;
        }
        try {
            handler.handle(event, agent);
            ack(id);
        } catch (RuntimeException e) {
            // Left pending: the reclaimer retries it once it has been idle for a while.
            log.warn("Agent event {} ({} seq {}) failed, will retry: {}", id, event.type(), event.seq(), e.getMessage());
        }
    }

    @Scheduled(fixedDelay = 30_000, initialDelay = 15_000)
    void reclaim() {
        try {
            var pending = redis.opsForStream().pending(stream, group, Range.unbounded(), 100);
            for (PendingMessage message : pending) {
                if (message.getElapsedTimeSinceLastDelivery().compareTo(RECLAIM_IDLE) < 0) {
                    continue;
                }
                if (message.getTotalDeliveryCount() >= MAX_DELIVERIES) {
                    log.error("Dead-lettering agent event {} after {} deliveries", message.getId(),
                            message.getTotalDeliveryCount());
                    ack(message.getId());
                    continue;
                }
                List<MapRecord<String, Object, Object>> claimed =
                        redis.opsForStream().claim(stream, group, consumer, RECLAIM_IDLE, message.getId());
                for (MapRecord<String, Object, Object> record : claimed) {
                    Object payload = record.getValue().get("event");
                    process(record.getId(), payload == null ? "" : payload.toString());
                }
            }
        } catch (RuntimeException e) {
            log.debug("Reclaim pass failed: {}", e.getMessage());
        }
    }

    private AgentEvent toEvent(Map<String, Object> raw) {
        @SuppressWarnings("unchecked")
        Map<String, Object> data = raw.get("data") instanceof Map<?, ?> m ? (Map<String, Object>) m : Map.of();
        return new AgentEvent(
                UUID.fromString(raw.get("event_id").toString()),
                raw.get("type").toString(),
                raw.get("run_id").toString(),
                UUID.fromString(raw.get("incident_id").toString()),
                ((Number) raw.get("seq")).intValue(),
                Instant.parse(raw.get("at").toString()),
                data);
    }

    private void ack(RecordId id) {
        redis.opsForStream().acknowledge(stream, group, id);
    }

    private void ensureGroup() {
        try {
            // MKSTREAM: the stream may not exist until agent-service publishes its first event.
            redis.execute((RedisCallback<String>) connection -> connection.streamCommands()
                    .xGroupCreate(stream.getBytes(StandardCharsets.UTF_8), group, ReadOffset.from("0"), true));
        } catch (RedisSystemException e) {
            if (!String.valueOf(e.getMostSpecificCause().getMessage()).contains("BUSYGROUP")) {
                throw e;
            }
        }
    }

    private static String consumerName() {
        try {
            return InetAddress.getLocalHost().getHostName();
        } catch (Exception e) {
            return "platform-api-" + UUID.randomUUID().toString().substring(0, 8);
        }
    }
}
