package dev.relay.incident;

import java.math.BigDecimal;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Timestamp;
import java.time.Instant;
import java.util.Collection;
import java.util.HashSet;
import java.util.List;
import java.util.Optional;
import java.util.Set;
import java.util.UUID;

import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

@Repository
public class IncidentRepository {

    private static final String COLUMNS = """
            id, number, title, service, severity, status, source, group_key, namespace, opened_at,
            resolved_at, run_id, summary, root_cause_category, root_cause_service, cost_usd, updated_at""";

    private final JdbcClient jdbc;

    IncidentRepository(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    public Optional<Incident> findById(UUID id) {
        return jdbc.sql("SELECT " + COLUMNS + " FROM incidents WHERE id = :id")
                .param("id", id)
                .query(IncidentRepository::map)
                .optional();
    }

    /** The active incident for an alert group, locked for the rest of the transaction. */
    public Optional<Incident> lockActiveByGroupKey(String groupKey) {
        return jdbc.sql("SELECT " + COLUMNS + """
                 FROM incidents
                WHERE group_key = :groupKey AND status <> 'RESOLVED'
                FOR UPDATE""")
                .param("groupKey", groupKey)
                .query(IncidentRepository::map)
                .optional();
    }

    /**
     * Opens an incident unless another transaction just opened one for the same
     * group (the partial unique index decides); empty means "lost the race".
     */
    public Optional<Incident> insertIfAbsent(UUID id, String title, String service, Severity severity, String source,
            String groupKey, String namespace, Instant openedAt) {
        return jdbc.sql("""
                INSERT INTO incidents (id, title, service, severity, status, source, group_key, namespace, opened_at)
                VALUES (:id, :title, :service, :severity, 'OPEN', :source, :groupKey, :namespace, :openedAt)
                ON CONFLICT (group_key) WHERE status <> 'RESOLVED' DO NOTHING
                """ + "RETURNING " + COLUMNS) // not inside the text block: it would strip the trailing space
                .param("id", id)
                .param("title", title)
                .param("service", service)
                .param("severity", severity.name())
                .param("source", source)
                .param("groupKey", groupKey)
                .param("namespace", namespace)
                .param("openedAt", Timestamp.from(openedAt))
                .query(IncidentRepository::map)
                .optional();
    }

    /** Newest first, keyset-paginated on (opened_at, id). */
    public List<Incident> page(Collection<IncidentStatus> statuses, Cursor after, int limit) {
        return jdbc.sql("SELECT " + COLUMNS + """
                 FROM incidents
                WHERE status IN (:statuses)
                  AND (CAST(:afterOpened AS timestamptz) IS NULL
                       OR (opened_at, id) < (CAST(:afterOpened AS timestamptz), CAST(:afterId AS uuid)))
                ORDER BY opened_at DESC, id DESC
                LIMIT :limit""")
                .param("statuses", statuses.stream().map(Enum::name).toList())
                .param("afterOpened", after == null ? null : Timestamp.from(after.openedAt()))
                .param("afterId", after == null ? null : after.id())
                .param("limit", limit)
                .query(IncidentRepository::map)
                .list();
    }

    /** The triage of the incident's latest run, as the agent reported it, with its run_id. */
    public void triage(UUID id, String triage) {
        jdbc.sql("UPDATE incidents SET triage = CAST(:triage AS jsonb), updated_at = now() WHERE id = :id")
                .param("triage", triage)
                .param("id", id)
                .update();
    }

    public Optional<String> triageOf(UUID id) {
        return jdbc.sql("SELECT triage::text FROM incidents WHERE id = :id AND triage IS NOT NULL")
                .param("id", id)
                .query(String.class)
                .optional();
    }

    public void escalate(UUID id, Severity severity) {
        jdbc.sql("UPDATE incidents SET severity = :severity, updated_at = now() WHERE id = :id")
                .param("severity", severity.name())
                .param("id", id)
                .update();
    }

    public void startRun(UUID id, String runId) {
        jdbc.sql("""
                UPDATE incidents SET status = 'INVESTIGATING', run_id = :runId, updated_at = now()
                WHERE id = :id AND status <> 'RESOLVED'""")
                .param("runId", runId)
                .param("id", id)
                .update();
    }

    /** Records the run on an incident that has none yet (a run that failed before it started). */
    public void attachRun(UUID id, String runId) {
        jdbc.sql("UPDATE incidents SET run_id = :runId, updated_at = now() WHERE id = :id AND run_id IS NULL")
                .param("runId", runId)
                .param("id", id)
                .update();
    }

    public void diagnose(UUID id, String summary, String category, String service) {
        jdbc.sql("""
                UPDATE incidents
                   SET status = 'DIAGNOSED', summary = :summary, root_cause_category = :category,
                       root_cause_service = :service, updated_at = now()
                 WHERE id = :id AND status <> 'RESOLVED'""")
                .param("summary", summary)
                .param("category", category)
                .param("service", service)
                .param("id", id)
                .update();
    }

    /** The reviewer moved another hypothesis to the top: the incident's root cause follows it. */
    public void rerank(UUID id, String category, String service) {
        jdbc.sql("""
                UPDATE incidents SET root_cause_category = :category, root_cause_service = :service, updated_at = now()
                 WHERE id = :id AND status <> 'RESOLVED'""")
                .param("category", category)
                .param("service", service)
                .param("id", id)
                .update();
    }

    /** The agent asked to act: the incident waits for a human. */
    public void awaitApproval(UUID id) {
        jdbc.sql("""
                UPDATE incidents SET status = 'AWAITING_APPROVAL', updated_at = now()
                 WHERE id = :id AND status IN ('INVESTIGATING', 'DIAGNOSED')""")
                .param("id", id)
                .update();
    }

    /** A human decided: back to DIAGNOSED (the decision itself is on the timeline). */
    public void approvalDecided(UUID id) {
        jdbc.sql("UPDATE incidents SET status = 'DIAGNOSED', updated_at = now() WHERE id = :id AND status = 'AWAITING_APPROVAL'")
                .param("id", id)
                .update();
    }

    public void fail(UUID id) {
        jdbc.sql("UPDATE incidents SET status = 'FAILED', updated_at = now() WHERE id = :id AND status <> 'RESOLVED'")
                .param("id", id)
                .update();
    }

    public void addCost(UUID id, BigDecimal costUsd) {
        jdbc.sql("UPDATE incidents SET cost_usd = cost_usd + :cost, updated_at = now() WHERE id = :id")
                .param("cost", costUsd)
                .param("id", id)
                .update();
    }

    public boolean resolve(UUID id, Instant at) {
        return jdbc.sql("""
                UPDATE incidents SET status = 'RESOLVED', resolved_at = :at, updated_at = now()
                WHERE id = :id AND status <> 'RESOLVED'""")
                .param("at", Timestamp.from(at))
                .param("id", id)
                .update() == 1;
    }

    // --- alerts ---------------------------------------------------------------------------------

    public void upsertAlert(UUID incidentId, AlertItem alert, String labelsJson) {
        jdbc.sql("""
                INSERT INTO incident_alerts (incident_id, fingerprint, name, service, severity, status, summary,
                                             starts_at, ends_at, labels)
                VALUES (:incidentId, :fingerprint, :name, :service, :severity, :status, :summary,
                        :startsAt, :endsAt, CAST(:labels AS jsonb))
                ON CONFLICT (incident_id, fingerprint) DO UPDATE
                   SET status = EXCLUDED.status, summary = EXCLUDED.summary, ends_at = EXCLUDED.ends_at,
                       severity = EXCLUDED.severity, updated_at = now()""")
                .param("incidentId", incidentId)
                .param("fingerprint", alert.fingerprint())
                .param("name", alert.name())
                .param("service", alert.service())
                .param("severity", alert.severity().name())
                .param("status", alert.firing() ? "firing" : "resolved")
                .param("summary", alert.summary())
                .param("startsAt", alert.startsAt() == null ? null : Timestamp.from(alert.startsAt()))
                .param("endsAt", alert.endsAt() == null ? null : Timestamp.from(alert.endsAt()))
                .param("labels", labelsJson)
                .update();
    }

    /** Alert episodes ({@link AlertItem#instanceKey}) of the group's incidents resolved in the last day. */
    public Set<String> recentlyResolvedAlerts(String groupKey) {
        return new HashSet<>(jdbc.sql("""
                SELECT a.fingerprint, a.starts_at
                  FROM incident_alerts a JOIN incidents i ON i.id = a.incident_id
                 WHERE i.group_key = :groupKey AND i.status = 'RESOLVED'
                   AND i.resolved_at > now() - interval '1 day' AND a.starts_at IS NOT NULL""")
                .param("groupKey", groupKey)
                .query((rs, n) -> AlertItem.instanceKey(rs.getString("fingerprint"), instant(rs, "starts_at")))
                .list());
    }

    public List<IncidentAlert> alerts(UUID incidentId) {
        return jdbc.sql("""
                SELECT fingerprint, name, service, severity, status, summary, starts_at, ends_at
                  FROM incident_alerts WHERE incident_id = :id ORDER BY starts_at NULLS LAST, name""")
                .param("id", incidentId)
                .query((rs, n) -> new IncidentAlert(rs.getString("fingerprint"), rs.getString("name"),
                        rs.getString("service"), rs.getString("severity"), rs.getString("status"),
                        rs.getString("summary"), instant(rs, "starts_at"), instant(rs, "ends_at")))
                .list();
    }

    // --- timeline -------------------------------------------------------------------------------

    public void addEvent(UUID incidentId, Instant at, String kind, String message, String dataJson) {
        jdbc.sql("""
                INSERT INTO incident_events (incident_id, at, kind, message, data)
                VALUES (:incidentId, :at, :kind, :message, CAST(:data AS jsonb))""")
                .param("incidentId", incidentId)
                .param("at", Timestamp.from(at))
                .param("kind", kind)
                .param("message", message)
                .param("data", dataJson)
                .update();
    }

    public List<TimelineEvent> timeline(UUID incidentId) {
        return jdbc.sql("SELECT id, at, kind, message, data FROM incident_events WHERE incident_id = :id ORDER BY at, id")
                .param("id", incidentId)
                .query((rs, n) -> new TimelineEvent(rs.getLong("id"), instant(rs, "at"), rs.getString("kind"),
                        rs.getString("message"), rs.getString("data")))
                .list();
    }

    static Incident map(ResultSet rs, int row) throws SQLException {
        return new Incident(
                rs.getObject("id", UUID.class),
                rs.getLong("number"),
                rs.getString("title"),
                rs.getString("service"),
                Severity.valueOf(rs.getString("severity")),
                IncidentStatus.valueOf(rs.getString("status")),
                rs.getString("source"),
                rs.getString("group_key"),
                rs.getString("namespace"),
                instant(rs, "opened_at"),
                instant(rs, "resolved_at"),
                rs.getString("run_id"),
                rs.getString("summary"),
                rs.getString("root_cause_category"),
                rs.getString("root_cause_service"),
                rs.getBigDecimal("cost_usd"),
                instant(rs, "updated_at"));
    }

    static Instant instant(ResultSet rs, String column) throws SQLException {
        Timestamp ts = rs.getTimestamp(column);
        return ts == null ? null : ts.toInstant();
    }
}
