package dev.relay.postmortem;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.sql.Timestamp;
import java.time.Instant;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;
import java.util.stream.Collectors;

import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import dev.relay.incident.Incident;
import dev.relay.incident.IncidentRepository;
import tools.jackson.databind.json.JsonMapper;

/**
 * Stores postmortems and feeds them to the knowledge base: each section becomes a
 * knowledge chunk (kind 'postmortem') without an embedding, which the runbooks MCP
 * server fills in within seconds. From then on, investigations can find how this
 * incident ended.
 */
@Service
public class PostmortemService {

    private final JdbcClient jdbc;
    private final IncidentRepository incidents;
    private final JsonMapper json;

    PostmortemService(JdbcClient jdbc, IncidentRepository incidents, JsonMapper json) {
        this.jdbc = jdbc;
        this.incidents = incidents;
        this.json = json;
    }

    public Optional<Postmortem> find(UUID incidentId) {
        return jdbc.sql("""
                SELECT incident_id, run_id, title, document::text AS document, markdown, model, written_at
                  FROM postmortems WHERE incident_id = :id""")
                .param("id", incidentId)
                .query((rs, n) -> new Postmortem(rs.getObject("incident_id", UUID.class), rs.getString("run_id"),
                        rs.getString("title"), rs.getString("document"), rs.getString("markdown"), rs.getString("model"),
                        rs.getTimestamp("written_at").toInstant()))
                .optional();
    }

    /** A postmortem.written agent event: store it, index it, note it on the timeline. */
    @Transactional
    public void written(UUID incidentId, String runId, Instant at, Map<String, Object> data) {
        @SuppressWarnings("unchecked")
        Map<String, Object> document = (Map<String, Object>) data.get("postmortem");
        String title = String.valueOf(document.get("title"));
        jdbc.sql("""
                INSERT INTO postmortems (incident_id, run_id, title, document, markdown, model, written_at)
                VALUES (:id, :run, :title, CAST(:document AS jsonb), :markdown, :model, :at)
                ON CONFLICT (incident_id) DO UPDATE
                SET run_id = excluded.run_id, title = excluded.title, document = excluded.document,
                    markdown = excluded.markdown, model = excluded.model, written_at = excluded.written_at""")
                .param("id", incidentId)
                .param("run", runId)
                .param("title", title)
                .param("document", json.writeValueAsString(document))
                .param("markdown", String.valueOf(data.get("markdown")))
                .param("model", String.valueOf(data.get("model")))
                .param("at", Timestamp.from(at))
                .update();
        String key = incidents.findById(incidentId).map(Incident::key).orElse(incidentId.toString());
        index(key, title, document);
        incidents.addEvent(incidentId, at, "postmortem.written", "Postmortem written: " + title,
                json.writeValueAsString(Map.of("run_id", runId)));
    }

    private void index(String key, String title, Map<String, Object> d) {
        String docId = "postmortem:" + key;
        List<String[]> sections = new ArrayList<>();
        sections.add(new String[] {"Summary", d.get("summary") + "\n\nImpact: " + d.get("impact")});
        sections.add(new String[] {"Root cause", d.get("root_cause") + "\n\nDetection: " + d.get("detection")});
        sections.add(new String[] {"Resolution", d.get("resolution") + "\n\nAction items:\n" + lines(d.get("action_items"), "item")});
        sections.add(new String[] {"Timeline", lines(d.get("timeline"), "event")});
        sections.add(new String[] {"Lessons", lines(d.get("lessons"), null)});
        jdbc.sql("DELETE FROM knowledge_chunks WHERE doc_id = :doc").param("doc", docId).update();
        for (int position = 0; position < sections.size(); position++) {
            String[] section = sections.get(position);
            jdbc.sql("""
                    INSERT INTO knowledge_chunks (doc_id, kind, title, section, position, text, checksum)
                    VALUES (:doc, 'postmortem', :title, :section, :position, :text, :checksum)""")
                    .param("doc", docId)
                    .param("title", key + ": " + title)
                    .param("section", section[0])
                    .param("position", position)
                    .param("text", section[1])
                    .param("checksum", sha256(section[0] + "\n" + section[1]).substring(0, 32))
                    .update();
        }
    }

    @SuppressWarnings("unchecked")
    private static String lines(Object items, String field) {
        if (!(items instanceof List<?> list)) {
            return "";
        }
        return list.stream()
                .map(item -> field != null && item instanceof Map<?, ?> m
                        ? (m.containsKey("at") ? m.get("at") + " " : "") + m.get(field)
                        : String.valueOf(item))
                .map(line -> "- " + line)
                .collect(Collectors.joining("\n"));
    }

    private static String sha256(String text) {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(text.getBytes(StandardCharsets.UTF_8)));
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
    }
}
