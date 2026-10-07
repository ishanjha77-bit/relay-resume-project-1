package dev.relay.eval;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.Map;
import java.util.UUID;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;

/** One scenario of one eval batch, as scored by evals/runner.py. */
public record EvalRun(
        UUID id,
        @NotBlank String batch,
        @NotBlank String scenario,
        @NotBlank String category,
        @NotNull @Pattern(regexp = "scored|agent_failed|no_alert|timeout") String status,
        Boolean correct,
        Boolean correctTop3,
        String topCategory,
        String topService,
        BigDecimal confidence,
        BigDecimal fixScore,
        BigDecimal citationsVerified,
        Boolean injectionOk,
        Integer steps,
        Integer llmCalls,
        Integer promptTokens,
        Integer outputTokens,
        Integer cacheReadTokens,
        BigDecimal costUsd,
        BigDecimal seconds,
        BigDecimal agentSeconds,
        String model,
        UUID incidentId,
        String runId,
        @NotNull Instant runAt,
        Map<String, Object> details) {
}
