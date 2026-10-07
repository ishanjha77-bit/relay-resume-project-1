package dev.relay.agent;

import java.math.BigDecimal;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Timestamp;
import java.util.List;
import java.util.UUID;

import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

@Repository
public class AgentRepository {

    private final JdbcClient jdbc;

    AgentRepository(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    /** @return false if this (run_id, seq) was already recorded — a redelivered event */
    public boolean insertStep(UUID incidentId, AgentStep step) {
        return jdbc.sql("""
                INSERT INTO agent_steps (incident_id, run_id, seq, agent, kind, tool, evidence_id, input_json,
                                         output_json, tokens_in, tokens_out, cache_read_tokens, cost_usd,
                                         latency_ms, created_at)
                VALUES (:incidentId, :runId, :seq, :agent, :kind, :tool, :evidenceId, CAST(:input AS jsonb),
                        CAST(:output AS jsonb), :tokensIn, :tokensOut, :cacheRead, :cost, :latency, :createdAt)
                ON CONFLICT (run_id, seq) DO NOTHING""")
                .param("incidentId", incidentId)
                .param("runId", step.runId())
                .param("seq", step.seq())
                .param("agent", step.agent())
                .param("kind", step.kind())
                .param("tool", step.tool())
                .param("evidenceId", step.evidenceId())
                .param("input", step.input())
                .param("output", step.output())
                .param("tokensIn", step.tokensIn())
                .param("tokensOut", step.tokensOut())
                .param("cacheRead", step.cacheReadTokens())
                .param("cost", step.costUsd())
                .param("latency", step.latencyMs())
                .param("createdAt", Timestamp.from(step.createdAt()))
                .update() == 1;
    }

    public List<AgentStep> steps(UUID incidentId, String runId, int afterSeq, int limit) {
        return jdbc.sql("""
                SELECT run_id, seq, agent, kind, tool, evidence_id, input_json::text AS input,
                       output_json::text AS output, tokens_in, tokens_out, cache_read_tokens, cost_usd,
                       latency_ms, created_at
                  FROM agent_steps
                 WHERE incident_id = :incidentId AND run_id = :runId AND seq > :afterSeq
                 ORDER BY seq
                 LIMIT :limit""")
                .param("incidentId", incidentId)
                .param("runId", runId)
                .param("afterSeq", afterSeq)
                .param("limit", limit)
                .query(AgentRepository::mapStep)
                .list();
    }

    public void replaceHypotheses(UUID incidentId, String runId, List<Hypothesis> hypotheses) {
        jdbc.sql("DELETE FROM hypotheses WHERE run_id = :runId").param("runId", runId).update();
        for (Hypothesis h : hypotheses) {
            jdbc.sql("""
                    INSERT INTO hypotheses (incident_id, run_id, rank, category, service, component, summary,
                                            confidence, evidence_json, suggested_fix, verdict, original_confidence, review)
                    VALUES (:incidentId, :runId, :rank, :category, :service, :component, :summary, :confidence,
                            CAST(:evidence AS jsonb), :fix, :verdict, :originalConfidence, CAST(:review AS jsonb))""")
                    .param("incidentId", incidentId)
                    .param("runId", runId)
                    .param("rank", h.rank())
                    .param("category", h.category())
                    .param("service", h.service())
                    .param("component", h.component())
                    .param("summary", h.summary())
                    .param("confidence", h.confidence())
                    .param("evidence", h.evidence())
                    .param("fix", h.suggestedFix())
                    .param("verdict", h.verdict())
                    .param("originalConfidence", h.originalConfidence())
                    .param("review", h.review())
                    .update();
        }
    }

    public List<Hypothesis> hypotheses(UUID incidentId, String runId) {
        return jdbc.sql("""
                SELECT run_id, rank, category, service, component, summary, confidence, evidence_json::text AS evidence,
                       suggested_fix, verdict, original_confidence, review::text AS review
                  FROM hypotheses WHERE incident_id = :incidentId AND run_id = :runId ORDER BY rank""")
                .param("incidentId", incidentId)
                .param("runId", runId)
                .query((rs, n) -> new Hypothesis(rs.getString("run_id"), rs.getInt("rank"), rs.getString("category"),
                        rs.getString("service"), rs.getString("component"), rs.getString("summary"),
                        rs.getBigDecimal("confidence"), rs.getString("evidence"), rs.getString("suggested_fix"),
                        rs.getString("verdict"), rs.getBigDecimal("original_confidence"), rs.getString("review")))
                .list();
    }

    private static AgentStep mapStep(ResultSet rs, int row) throws SQLException {
        return new AgentStep(
                rs.getString("run_id"),
                rs.getInt("seq"),
                rs.getString("agent"),
                rs.getString("kind"),
                rs.getString("tool"),
                rs.getString("evidence_id"),
                rs.getString("input"),
                rs.getString("output"),
                (Integer) rs.getObject("tokens_in"),
                (Integer) rs.getObject("tokens_out"),
                (Integer) rs.getObject("cache_read_tokens"),
                rs.getBigDecimal("cost_usd"),
                (Integer) rs.getObject("latency_ms"),
                rs.getTimestamp("created_at").toInstant());
    }

    static BigDecimal decimal(Double value) {
        return value == null ? null : BigDecimal.valueOf(value);
    }
}
