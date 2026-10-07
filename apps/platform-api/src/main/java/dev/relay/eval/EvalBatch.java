package dev.relay.eval;

import java.math.BigDecimal;
import java.time.Instant;

/**
 * The scorecard of one batch. Accuracy counts every run where Relay had a
 * chance to answer: a verdict (right or wrong) or an investigation that failed.
 * Runs where the sandbox never alerted are reported, not scored.
 */
public record EvalBatch(
        String batch,
        int runs,
        int answered,
        BigDecimal accuracy,
        BigDecimal accuracyTop3,
        BigDecimal medianSeconds,
        BigDecimal medianAgentSeconds,
        BigDecimal meanSteps,
        BigDecimal meanLlmCalls,
        BigDecimal meanCostUsd,
        BigDecimal totalCostUsd,
        Long totalTokens,
        String models,
        Instant startedAt,
        Instant finishedAt) {
}
