package dev.relay.incident;

import java.time.Instant;

import com.fasterxml.jackson.annotation.JsonRawValue;

public record TimelineEvent(long id, Instant at, String kind, String message, @JsonRawValue String data) {
}
