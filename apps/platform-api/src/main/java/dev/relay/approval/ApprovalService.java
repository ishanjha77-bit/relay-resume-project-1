package dev.relay.approval;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Clock;
import java.time.Instant;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

import org.springframework.context.ApplicationEventPublisher;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

import dev.relay.RelayProperties;
import dev.relay.incident.Incident;
import dev.relay.incident.IncidentChanged;
import dev.relay.incident.IncidentRepository;
import dev.relay.outbox.Outbox;
import tools.jackson.databind.json.JsonMapper;

/**
 * The approval gate between an agent's proposal and its execution.
 *
 * An agent's request arrives as an approval.requested event and parks the
 * incident in AWAITING_APPROVAL. A decision is made once, by a user with the
 * APPROVER role, and goes to the agent service through the outbox (stream
 * relay.approvals, contracts/schemas/approval-decision.schema.json). An approval
 * carries a signed token bound to the action's exact text, so the action that
 * runs is the one the approver read.
 */
@Service
public class ApprovalService {

    private final ApprovalRepository approvals;
    private final IncidentRepository incidents;
    private final ApprovalTokens tokens;
    private final Outbox outbox;
    private final JsonMapper json;
    private final RelayProperties properties;
    private final ApplicationEventPublisher events;
    private final Clock clock;

    ApprovalService(ApprovalRepository approvals, IncidentRepository incidents, ApprovalTokens tokens, Outbox outbox,
            JsonMapper json, RelayProperties properties, ApplicationEventPublisher events, Clock clock) {
        this.approvals = approvals;
        this.incidents = incidents;
        this.tokens = tokens;
        this.outbox = outbox;
        this.json = json;
        this.properties = properties;
        this.events = events;
        this.clock = clock;
    }

    public List<Approval> forIncident(UUID incidentId) {
        return approvals.forIncident(incidentId);
    }

    /** An agent asks to act (agent event approval.requested). Returns false for a redelivered request. */
    @Transactional
    public boolean requested(UUID incidentId, String runId, String agent, Instant at, Map<String, Object> data) {
        String action = String.valueOf(data.get("action"));
        Approval approval = new Approval(UUID.fromString(String.valueOf(data.get("approval_id"))), incidentId, runId,
                String.valueOf(data.get("kind")), String.valueOf(data.get("title")), String.valueOf(data.get("risk")),
                String.valueOf(data.getOrDefault("rationale", "")), String.valueOf(data.getOrDefault("diff", "")),
                action, sha256(action), agent, at, ApprovalStatus.PENDING, null, null, null, null, null);
        if (!approvals.insert(approval)) {
            return false;
        }
        incidents.awaitApproval(incidentId);
        incidents.addEvent(incidentId, at, "approval.requested",
                "Relay asks to: %s (risk %s)".formatted(approval.title(), approval.risk()),
                json.writeValueAsString(Map.of("approval_id", approval.id().toString())));
        return true;
    }

    /** The agent reports the approved action's outcome (action.executed / action.failed). */
    @Transactional
    public boolean finished(UUID incidentId, UUID approvalId, Instant at, Map<String, Object> result, String error) {
        boolean ok = error == null;
        if (!approvals.finish(approvalId, ok ? ApprovalStatus.EXECUTED : ApprovalStatus.FAILED,
                ok ? json.writeValueAsString(result) : null, error)) {
            return false;
        }
        String message = ok
                ? result.get("url") != null
                        ? "Draft pull request #%s opened: %s".formatted(result.get("pull_request"), result.get("url"))
                        : "Approved action done"
                : "Approved action failed: " + error;
        incidents.addEvent(incidentId, at, ok ? "action.executed" : "action.failed", message,
                json.writeValueAsString(ok ? result : Map.of("error", error)));
        return true;
    }

    /**
     * Rejects whatever still waits for a decision when the incident is resolved: an
     * approval of a fix for a closed incident would act on a sandbox that has moved on.
     * The rejection reaches the paused run like any other, which then ends.
     */
    @Transactional
    public void closePending(UUID incidentId, String by) {
        for (Approval approval : approvals.forIncident(incidentId)) {
            if (approval.status() == ApprovalStatus.PENDING) {
                decide(incidentId, approval.id(), false, "The incident was resolved before anyone decided.", by);
            }
        }
    }

    /** A human decision. 404 if the approval isn't the incident's, 409 if it was already decided. */
    @Transactional
    public Approval decide(UUID incidentId, UUID approvalId, boolean approve, String reason, String by) {
        Approval approval = approvals.lock(approvalId)
                .filter(a -> a.incidentId().equals(incidentId))
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "approval " + approvalId + " not found"));
        if (approval.status() != ApprovalStatus.PENDING) {
            throw new ResponseStatusException(HttpStatus.CONFLICT,
                    "approval already decided: " + approval.status().name().toLowerCase());
        }
        Instant now = clock.instant();
        ApprovalStatus status = approve ? ApprovalStatus.APPROVED : ApprovalStatus.REJECTED;
        approvals.decide(approvalId, status, by, reason, now);
        incidents.approvalDecided(incidentId);
        String incidentKey = incidents.findById(incidentId).map(Incident::key).orElse(incidentId.toString());
        incidents.addEvent(incidentId, now, approve ? "approval.approved" : "approval.rejected",
                "%s %s: %s%s".formatted(by, approve ? "approved" : "rejected", approval.title(),
                        reason == null || reason.isBlank() ? "" : " (" + reason + ")"),
                json.writeValueAsString(Map.of("approval_id", approvalId.toString())));

        Map<String, Object> decision = new LinkedHashMap<>();
        decision.put("approval_id", approvalId.toString());
        decision.put("incident_id", incidentId.toString());
        decision.put("run_id", approval.runId());
        decision.put("decision", approve ? "approved" : "rejected");
        decision.put("decided_by", by);
        decision.put("reason", reason);
        decision.put("decided_at", now.toString());
        decision.put("action", approval.action());
        if (approve) {
            decision.put("token", tokens.issue(approval, by, incidentKey, now));
        }
        outbox.add(properties.streams().approvals(), json.writeValueAsString(decision));
        events.publishEvent(new IncidentChanged(incidentId));
        return approvals.lock(approvalId).orElseThrow();
    }

    static String sha256(String text) {
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256").digest(text.getBytes(StandardCharsets.UTF_8));
            return HexFormat.of().formatHex(digest);
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
    }
}
