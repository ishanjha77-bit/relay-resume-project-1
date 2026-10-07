package dev.relay.approval;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Timestamp;
import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;

import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

import tools.jackson.core.type.TypeReference;
import tools.jackson.databind.json.JsonMapper;

@Repository
class ApprovalRepository {

    private static final String COLUMNS = """
            id, incident_id, run_id, kind, title, risk, rationale, diff, action, action_sha256,
            requested_by_agent, requested_at, status, decided_by, decision_reason, decided_at,
            result::text AS result, error""";

    private final JdbcClient jdbc;
    private final JsonMapper json;

    ApprovalRepository(JdbcClient jdbc, JsonMapper json) {
        this.jdbc = jdbc;
        this.json = json;
    }

    /** False when the request is already stored: requests are redelivered, approvals are not repeated. */
    boolean insert(Approval a) {
        return jdbc.sql("""
                INSERT INTO approvals (id, incident_id, run_id, kind, title, risk, rationale, diff, action,
                                       action_sha256, requested_by_agent, requested_at)
                VALUES (:id, :incident, :run, :kind, :title, :risk, :rationale, :diff, :action, :sha, :agent, :at)
                ON CONFLICT (id) DO NOTHING""")
                .param("id", a.id())
                .param("incident", a.incidentId())
                .param("run", a.runId())
                .param("kind", a.kind())
                .param("title", a.title())
                .param("risk", a.risk())
                .param("rationale", a.rationale())
                .param("diff", a.diff())
                .param("action", a.action())
                .param("sha", a.actionSha256())
                .param("agent", a.requestedByAgent())
                .param("at", Timestamp.from(a.requestedAt()))
                .update() == 1;
    }

    Optional<Approval> lock(UUID id) {
        return jdbc.sql("SELECT " + COLUMNS + " FROM approvals WHERE id = :id FOR UPDATE")
                .param("id", id).query(this::map).optional();
    }

    List<Approval> forIncident(UUID incidentId) {
        return jdbc.sql("SELECT " + COLUMNS + " FROM approvals WHERE incident_id = :id ORDER BY requested_at DESC")
                .param("id", incidentId).query(this::map).list();
    }

    /** PENDING → APPROVED | REJECTED, once. */
    boolean decide(UUID id, ApprovalStatus status, String by, String reason, Instant at) {
        return jdbc.sql("""
                UPDATE approvals SET status = :status, decided_by = :by, decision_reason = :reason,
                                     decided_at = :at, updated_at = now()
                 WHERE id = :id AND status = 'PENDING'""")
                .param("status", status.name())
                .param("by", by)
                .param("reason", reason)
                .param("at", Timestamp.from(at))
                .param("id", id)
                .update() == 1;
    }

    /** APPROVED → EXECUTED | FAILED, once: a redelivered report changes nothing. */
    boolean finish(UUID id, ApprovalStatus status, String resultJson, String error) {
        return jdbc.sql("""
                UPDATE approvals SET status = :status, result = CAST(:result AS jsonb), error = :error,
                                     updated_at = now()
                 WHERE id = :id AND status = 'APPROVED'""")
                .param("status", status.name())
                .param("result", resultJson)
                .param("error", error)
                .param("id", id)
                .update() == 1;
    }

    private Approval map(ResultSet rs, int row) throws SQLException {
        String result = rs.getString("result");
        Timestamp decidedAt = rs.getTimestamp("decided_at");
        return new Approval(
                rs.getObject("id", UUID.class),
                rs.getObject("incident_id", UUID.class),
                rs.getString("run_id"),
                rs.getString("kind"),
                rs.getString("title"),
                rs.getString("risk"),
                rs.getString("rationale"),
                rs.getString("diff"),
                rs.getString("action"),
                rs.getString("action_sha256"),
                rs.getString("requested_by_agent"),
                rs.getTimestamp("requested_at").toInstant(),
                ApprovalStatus.valueOf(rs.getString("status")),
                rs.getString("decided_by"),
                rs.getString("decision_reason"),
                decidedAt == null ? null : decidedAt.toInstant(),
                result == null ? null : json.readValue(result, new TypeReference<Map<String, Object>>() {
                }),
                rs.getString("error"));
    }
}
