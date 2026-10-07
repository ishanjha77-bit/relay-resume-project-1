package dev.relay.agent;

import java.math.BigDecimal;

import com.fasterxml.jackson.annotation.JsonRawValue;

/**
 * @param originalConfidence what the investigator stated, when the reviewer lowered it (else null)
 * @param review             the reviewer's verdict and reason (null when not reviewed)
 */
public record Hypothesis(
        String runId,
        int rank,
        String category,
        String service,
        String component,
        String summary,
        BigDecimal confidence,
        @JsonRawValue String evidence,
        String suggestedFix,
        String verdict,
        BigDecimal originalConfidence,
        @JsonRawValue String review) {
}
