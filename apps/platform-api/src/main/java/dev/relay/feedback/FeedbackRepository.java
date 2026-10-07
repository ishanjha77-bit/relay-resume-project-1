package dev.relay.feedback;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;

import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

/** Responders' votes on what the agents showed them (V8, V9). One vote per person and thing. */
@Repository
public class FeedbackRepository {

    private final JdbcClient jdbc;

    FeedbackRepository(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    /** +1, -1, or 0 to take the vote back. */
    void voteDocument(UUID incidentId, String docId, String username, int vote) {
        if (vote == 0) {
            jdbc.sql("DELETE FROM document_votes WHERE doc_id = :doc AND incident_id = :incident AND username = :user")
                    .param("doc", docId).param("incident", incidentId).param("user", username)
                    .update();
            return;
        }
        jdbc.sql("""
                INSERT INTO document_votes (doc_id, incident_id, username, vote) VALUES (:doc, :incident, :user, :vote)
                ON CONFLICT (doc_id, incident_id, username) DO UPDATE SET vote = EXCLUDED.vote, voted_at = now()""")
                .param("doc", docId).param("incident", incidentId).param("user", username).param("vote", vote)
                .update();
    }

    void voteHypothesis(UUID incidentId, String runId, String category, String service, String username, int vote) {
        if (vote == 0) {
            jdbc.sql("""
                    DELETE FROM hypothesis_votes WHERE incident_id = :incident AND run_id = :run
                       AND category = :category AND service = :service AND username = :user""")
                    .param("incident", incidentId).param("run", runId).param("category", category)
                    .param("service", service).param("user", username)
                    .update();
            return;
        }
        jdbc.sql("""
                INSERT INTO hypothesis_votes (incident_id, run_id, category, service, username, vote)
                VALUES (:incident, :run, :category, :service, :user, :vote)
                ON CONFLICT (incident_id, run_id, category, service, username)
                DO UPDATE SET vote = EXCLUDED.vote, voted_at = now()""")
                .param("incident", incidentId).param("run", runId).param("category", category)
                .param("service", service).param("user", username).param("vote", vote)
                .update();
    }

    /**
     * The votes on an incident, as its page shows them: documents by doc_id across every
     * incident (what re-ranks search), hypotheses of this incident by "category:service",
     * each with the counts and the asking user's own vote.
     */
    public Feedback forIncident(UUID incidentId, String runId, String username) {
        Map<String, Votes> documents = new LinkedHashMap<>();
        jdbc.sql("""
                SELECT doc_id, count(*) FILTER (WHERE vote > 0) AS up, count(*) FILTER (WHERE vote < 0) AS down,
                       coalesce(max(vote) FILTER (WHERE username = :user AND incident_id = :incident), 0) AS mine
                  FROM document_votes
                 WHERE doc_id IN (SELECT doc_id FROM document_votes WHERE incident_id = :incident)
                    OR doc_id IN (SELECT jsonb_array_elements(triage -> 'related') ->> 'doc_id'
                                    FROM incidents WHERE id = :incident)
                 GROUP BY doc_id""")
                .param("user", username).param("incident", incidentId)
                .query((rs, n) -> documents.put(rs.getString("doc_id"),
                        new Votes(rs.getInt("up"), rs.getInt("down"), rs.getInt("mine"))))
                .list();
        Map<String, Votes> hypotheses = new LinkedHashMap<>();
        jdbc.sql("""
                SELECT category || ':' || service AS ref, count(*) FILTER (WHERE vote > 0) AS up,
                       count(*) FILTER (WHERE vote < 0) AS down,
                       coalesce(max(vote) FILTER (WHERE username = :user), 0) AS mine
                  FROM hypothesis_votes
                 WHERE incident_id = :incident AND run_id = :run
                 GROUP BY category, service""")
                .param("user", username).param("incident", incidentId).param("run", runId == null ? "" : runId)
                .query((rs, n) -> hypotheses.put(rs.getString("ref"),
                        new Votes(rs.getInt("up"), rs.getInt("down"), rs.getInt("mine"))))
                .list();
        return new Feedback(documents, hypotheses);
    }

    /** Up and down votes, and the asking user's own (+1, -1, or 0 for none). */
    public record Votes(int up, int down, int mine) {
    }

    public record Feedback(Map<String, Votes> documents, Map<String, Votes> hypotheses) {
    }
}
