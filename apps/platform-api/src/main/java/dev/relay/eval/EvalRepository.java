package dev.relay.eval;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Timestamp;
import java.util.List;
import java.util.Map;
import java.util.UUID;

import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

import tools.jackson.core.type.TypeReference;
import tools.jackson.databind.json.JsonMapper;

@Repository
class EvalRepository {

    private static final TypeReference<Map<String, Object>> MAP = new TypeReference<>() {
    };
    private static final String COLUMNS = """
            id, batch, scenario, category, status, correct, correct_top3, top_category, top_service, confidence,
            fix_score, citations_verified, injection_ok, steps, llm_calls, prompt_tokens, output_tokens,
            cache_read_tokens, cost_usd, seconds, agent_seconds, model, incident_id, run_id, run_at,
            details::text AS details""";

    private final JdbcClient jdbc;
    private final JsonMapper json;

    EvalRepository(JdbcClient jdbc, JsonMapper json) {
        this.jdbc = jdbc;
        this.json = json;
    }

    /** One row per (batch, scenario): re-running a scenario replaces its result. */
    EvalRun upsert(EvalRun run) {
        return jdbc.sql("""
                INSERT INTO eval_runs (id, batch, scenario, category, status, correct, correct_top3, top_category,
                                       top_service, confidence, fix_score, citations_verified, injection_ok, steps,
                                       llm_calls, prompt_tokens, output_tokens, cache_read_tokens, cost_usd, seconds,
                                       agent_seconds, model, incident_id, run_id, run_at, details)
                VALUES (:id, :batch, :scenario, :category, :status, :correct, :correctTop3, :topCategory,
                        :topService, :confidence, :fixScore, :citationsVerified, :injectionOk, :steps, :llmCalls,
                        :promptTokens, :outputTokens, :cacheReadTokens, :costUsd, :seconds, :agentSeconds, :model,
                        :incidentId, :runId, :runAt, CAST(:details AS jsonb))
                ON CONFLICT (batch, scenario) DO UPDATE SET
                    category = EXCLUDED.category, status = EXCLUDED.status, correct = EXCLUDED.correct,
                    correct_top3 = EXCLUDED.correct_top3, top_category = EXCLUDED.top_category,
                    top_service = EXCLUDED.top_service, confidence = EXCLUDED.confidence,
                    fix_score = EXCLUDED.fix_score, citations_verified = EXCLUDED.citations_verified,
                    injection_ok = EXCLUDED.injection_ok, steps = EXCLUDED.steps, llm_calls = EXCLUDED.llm_calls,
                    prompt_tokens = EXCLUDED.prompt_tokens, output_tokens = EXCLUDED.output_tokens,
                    cache_read_tokens = EXCLUDED.cache_read_tokens, cost_usd = EXCLUDED.cost_usd,
                    seconds = EXCLUDED.seconds, agent_seconds = EXCLUDED.agent_seconds, model = EXCLUDED.model,
                    incident_id = EXCLUDED.incident_id, run_id = EXCLUDED.run_id, run_at = EXCLUDED.run_at,
                    details = EXCLUDED.details
                """ + "RETURNING " + COLUMNS)
                .param("id", run.id() != null ? run.id() : UUID.randomUUID())
                .param("batch", run.batch())
                .param("scenario", run.scenario())
                .param("category", run.category())
                .param("status", run.status())
                .param("correct", run.correct())
                .param("correctTop3", run.correctTop3())
                .param("topCategory", run.topCategory())
                .param("topService", run.topService())
                .param("confidence", run.confidence())
                .param("fixScore", run.fixScore())
                .param("citationsVerified", run.citationsVerified())
                .param("injectionOk", run.injectionOk())
                .param("steps", run.steps())
                .param("llmCalls", run.llmCalls())
                .param("promptTokens", run.promptTokens())
                .param("outputTokens", run.outputTokens())
                .param("cacheReadTokens", run.cacheReadTokens())
                .param("costUsd", run.costUsd())
                .param("seconds", run.seconds())
                .param("agentSeconds", run.agentSeconds())
                .param("model", run.model())
                .param("incidentId", run.incidentId())
                .param("runId", run.runId())
                .param("runAt", Timestamp.from(run.runAt()))
                .param("details", json.writeValueAsString(run.details() != null ? run.details() : Map.of()))
                .query(this::map)
                .single();
    }

    List<EvalRun> list(String batch, int limit) {
        return jdbc.sql("SELECT " + COLUMNS + """
                 FROM eval_runs
                WHERE CAST(:batch AS text) IS NULL OR batch = :batch
                ORDER BY run_at DESC, scenario
                LIMIT :limit""")
                .param("batch", batch)
                .param("limit", limit)
                .query(this::map)
                .list();
    }

    List<EvalBatch> batches(int limit) {
        return jdbc.sql("""
                SELECT batch,
                       count(*) AS runs,
                       count(*) FILTER (WHERE status IN ('scored', 'agent_failed')) AS answered,
                       round(count(*) FILTER (WHERE correct)::numeric
                             / NULLIF(count(*) FILTER (WHERE status IN ('scored', 'agent_failed')), 0), 6) AS accuracy,
                       round(count(*) FILTER (WHERE correct_top3)::numeric
                             / NULLIF(count(*) FILTER (WHERE status IN ('scored', 'agent_failed')), 0), 6)
                             AS accuracy_top3,
                       percentile_cont(0.5) WITHIN GROUP (ORDER BY seconds)
                             FILTER (WHERE status = 'scored') AS median_seconds,
                       percentile_cont(0.5) WITHIN GROUP (ORDER BY agent_seconds)
                             FILTER (WHERE status = 'scored') AS median_agent_seconds,
                       round(avg(steps) FILTER (WHERE status = 'scored'), 1) AS mean_steps,
                       round(avg(llm_calls) FILTER (WHERE status = 'scored'), 1) AS mean_llm_calls,
                       round(avg(cost_usd) FILTER (WHERE status = 'scored'), 6) AS mean_cost_usd,
                       round(sum(cost_usd), 6) AS total_cost_usd,
                       sum(coalesce(prompt_tokens, 0) + coalesce(output_tokens, 0)) AS total_tokens,
                       string_agg(DISTINCT model, ', ') AS models,
                       min(run_at) AS started_at,
                       max(run_at) AS finished_at
                  FROM eval_runs
                 GROUP BY batch
                 ORDER BY max(run_at) DESC
                 LIMIT :limit""")
                .param("limit", limit)
                .query((rs, n) -> new EvalBatch(
                        rs.getString("batch"),
                        rs.getInt("runs"),
                        rs.getInt("answered"),
                        rs.getBigDecimal("accuracy"),
                        rs.getBigDecimal("accuracy_top3"),
                        rs.getBigDecimal("median_seconds"),
                        rs.getBigDecimal("median_agent_seconds"),
                        rs.getBigDecimal("mean_steps"),
                        rs.getBigDecimal("mean_llm_calls"),
                        rs.getBigDecimal("mean_cost_usd"),
                        rs.getBigDecimal("total_cost_usd"),
                        rs.getObject("total_tokens", Long.class),
                        rs.getString("models"),
                        rs.getTimestamp("started_at").toInstant(),
                        rs.getTimestamp("finished_at").toInstant()))
                .list();
    }

    private EvalRun map(ResultSet rs, int row) throws SQLException {
        Timestamp runAt = rs.getTimestamp("run_at");
        return new EvalRun(
                rs.getObject("id", UUID.class),
                rs.getString("batch"),
                rs.getString("scenario"),
                rs.getString("category"),
                rs.getString("status"),
                rs.getObject("correct", Boolean.class),
                rs.getObject("correct_top3", Boolean.class),
                rs.getString("top_category"),
                rs.getString("top_service"),
                rs.getBigDecimal("confidence"),
                rs.getBigDecimal("fix_score"),
                rs.getBigDecimal("citations_verified"),
                rs.getObject("injection_ok", Boolean.class),
                rs.getObject("steps", Integer.class),
                rs.getObject("llm_calls", Integer.class),
                rs.getObject("prompt_tokens", Integer.class),
                rs.getObject("output_tokens", Integer.class),
                rs.getObject("cache_read_tokens", Integer.class),
                rs.getBigDecimal("cost_usd"),
                rs.getBigDecimal("seconds"),
                rs.getBigDecimal("agent_seconds"),
                rs.getString("model"),
                rs.getObject("incident_id", UUID.class),
                rs.getString("run_id"),
                runAt == null ? null : runAt.toInstant(),
                json.readValue(rs.getString("details"), MAP));
    }
}
