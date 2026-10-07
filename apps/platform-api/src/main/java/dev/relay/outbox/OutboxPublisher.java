package dev.relay.outbox;

import java.util.List;
import java.util.Map;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.data.redis.connection.stream.StreamRecords;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionTemplate;

/**
 * Ships outbox rows to their Redis stream. Rows are claimed with
 * FOR UPDATE SKIP LOCKED so several replicas never publish the same row; if
 * Redis is down the transaction rolls back and the rows are retried. A crash
 * between XADD and the UPDATE re-publishes a row: delivery is at-least-once and
 * consumers are idempotent.
 */
@Component
class OutboxPublisher {

    private static final Logger log = LoggerFactory.getLogger(OutboxPublisher.class);

    private final JdbcClient jdbc;
    private final StringRedisTemplate redis;
    private final TransactionTemplate tx;
    private volatile boolean failing;

    OutboxPublisher(JdbcClient jdbc, StringRedisTemplate redis, TransactionTemplate tx) {
        this.jdbc = jdbc;
        this.redis = redis;
        this.tx = tx;
    }

    @Scheduled(fixedDelay = 250)
    void publish() {
        try {
            Integer shipped = tx.execute(status -> {
                List<Row> rows = jdbc.sql("""
                        SELECT id, stream, payload::text AS payload FROM outbox
                         WHERE published_at IS NULL ORDER BY id LIMIT 100
                           FOR UPDATE SKIP LOCKED""")
                        .query((rs, n) -> new Row(rs.getLong("id"), rs.getString("stream"), rs.getString("payload")))
                        .list();
                for (Row row : rows) {
                    redis.opsForStream().add(StreamRecords.string(Map.of("event", row.payload())).withStreamKey(row.stream()));
                }
                if (!rows.isEmpty()) {
                    jdbc.sql("UPDATE outbox SET published_at = now() WHERE id IN (:ids)")
                            .param("ids", rows.stream().map(Row::id).toList())
                            .update();
                }
                return rows.size();
            });
            if (failing) {
                log.info("Outbox publishing recovered");
                failing = false;
            }
            if (shipped != null && shipped > 0) {
                log.debug("Published {} outbox events", shipped);
            }
        } catch (RuntimeException e) {
            if (!failing) {
                log.warn("Outbox publishing failed, will retry: {}", e.getMessage());
                failing = true;
            }
        }
    }

    private record Row(long id, String stream, String payload) {
    }
}
