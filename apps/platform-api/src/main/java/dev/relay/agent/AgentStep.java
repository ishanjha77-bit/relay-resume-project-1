package dev.relay.agent;

import java.math.BigDecimal;
import java.time.Instant;

import com.fasterxml.jackson.annotation.JsonRawValue;

/** A persisted agent step as the API and the live stream present it. */
public record AgentStep(
        String runId,
        int seq,
        String agent,
        String kind,
        String tool,
        String evidenceId,
        @JsonRawValue String input,
        @JsonRawValue String output,
        Integer tokensIn,
        Integer tokensOut,
        Integer cacheReadTokens,
        BigDecimal costUsd,
        Integer latencyMs,
        Instant createdAt) {
}
