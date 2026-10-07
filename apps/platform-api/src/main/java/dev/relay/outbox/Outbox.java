package dev.relay.outbox;

import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Component;

/** Records an event in the current transaction; {@link OutboxPublisher} ships it after commit. */
@Component
public class Outbox {

    private final JdbcClient jdbc;

    Outbox(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    public void add(String stream, String payloadJson) {
        jdbc.sql("INSERT INTO outbox (stream, payload) VALUES (:stream, CAST(:payload AS jsonb))")
                .param("stream", stream)
                .param("payload", payloadJson)
                .update();
    }
}
