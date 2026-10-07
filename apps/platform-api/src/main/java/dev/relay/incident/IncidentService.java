package dev.relay.incident;

import java.time.Clock;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Optional;
import java.util.Set;
import java.util.UUID;
import java.util.function.Function;
import java.util.stream.Collectors;

import org.springframework.context.ApplicationEventPublisher;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import dev.relay.RelayProperties;
import dev.relay.agent.AgentRepository;
import dev.relay.agent.Hypothesis;
import dev.relay.approval.Approval;
import dev.relay.approval.ApprovalService;
import dev.relay.outbox.Outbox;
import tools.jackson.databind.json.JsonMapper;

@Service
public class IncidentService {

    private final IncidentRepository incidents;
    private final Outbox outbox;
    private final JsonMapper json;
    private final RelayProperties properties;
    private final ApplicationEventPublisher events;
    private final Clock clock;
    private final ApprovalService approvals;
    private final AgentRepository agents;

    IncidentService(IncidentRepository incidents, Outbox outbox, JsonMapper json, RelayProperties properties,
            ApplicationEventPublisher events, Clock clock, ApprovalService approvals, AgentRepository agents) {
        this.approvals = approvals;
        this.agents = agents;
        this.incidents = incidents;
        this.outbox = outbox;
        this.json = json;
        this.properties = properties;
        this.events = events;
        this.clock = clock;
    }

    public record Intake(UUID incidentId, long number, boolean opened, int alerts) {

        static Intake ignored() {
            return new Intake(null, 0, false, 0);
        }
    }

    /**
     * Attaches a batch of alerts to the active incident of its group, opening one
     * if there is none. Redeliveries are harmless: alerts are keyed by fingerprint
     * and timeline entries are written only when an alert changes state.
     */
    @Transactional
    public Intake ingest(AlertBatch batch) {
        Instant now = clock.instant();
        Optional<Incident> active = incidents.lockActiveByGroupKey(batch.groupKey());
        Incident incident;
        boolean opened = false;
        if (active.isPresent()) {
            incident = active.get();
        } else {
            List<AlertItem> firing = batch.alerts().stream().filter(AlertItem::firing).toList();
            // Alerts that keep firing for a while after someone resolved their incident
            // (rate windows take minutes to recover) are not a new incident; a new
            // alert, or one that resolved and fired again, is.
            Set<String> handled = incidents.recentlyResolvedAlerts(batch.groupKey());
            if (firing.stream().allMatch(a -> handled.contains(AlertItem.instanceKey(a.fingerprint(), a.startsAt())))) {
                return Intake.ignored(); // also "resolved" notifications for an incident already closed
            }
            Optional<Incident> inserted = openIncident(batch, firing);
            opened = inserted.isPresent();
            // Empty: a concurrent delivery for the same group won the insert; attach to its incident.
            incident = inserted.or(() -> incidents.lockActiveByGroupKey(batch.groupKey())).orElseThrow();
            if (opened) {
                incidents.addEvent(incident.id(), now, "incident.opened",
                        "Incident " + incident.key() + " opened from " + batch.source(), "{}");
            }
        }

        Map<String, IncidentAlert> known = incidents.alerts(incident.id()).stream()
                .collect(Collectors.toMap(IncidentAlert::fingerprint, Function.identity()));
        for (AlertItem alert : batch.alerts()) {
            IncidentAlert previous = known.get(alert.fingerprint());
            incidents.upsertAlert(incident.id(), alert, json.writeValueAsString(alert.labels()));
            String state = alert.firing() ? "firing" : "resolved";
            if (previous == null || !previous.status().equals(state)) {
                incidents.addEvent(incident.id(), now, "alert." + state, describe(alert), "{}");
            }
            if (alert.firing() && alert.severity().moreSevereThan(incident.severity())) {
                incidents.escalate(incident.id(), alert.severity());
            }
        }

        if (opened) {
            outbox.add(properties.streams().incidents(), incidentOpened(incident, batch, now));
        }
        events.publishEvent(new IncidentChanged(incident.id()));
        return new Intake(incident.id(), incident.number(), opened, batch.alerts().size());
    }

    @Transactional
    public boolean resolve(UUID id, String by, boolean postmortem) {
        approvals.closePending(id, by);
        Instant now = clock.instant();
        boolean changed = incidents.resolve(id, now);
        if (changed) {
            incidents.addEvent(id, now, "incident.resolved", "Resolved by " + by, "{}");
            Incident incident = incidents.findById(id).orElseThrow();
            // Only an investigated incident has a story worth a postmortem.
            if (postmortem && incident.runId() != null) {
                outbox.add(properties.streams().resolved(), incidentResolved(incident, by, now));
            }
            events.publishEvent(new IncidentChanged(id));
        }
        return changed;
    }

    /** The incident.resolved contract event (contracts/schemas/incident-resolved.schema.json). */
    private String incidentResolved(Incident incident, String by, Instant now) {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("id", incident.id().toString());
        body.put("key", incident.key());
        body.put("title", incident.title());
        body.put("service", incident.service());
        body.put("severity", incident.severity().name().toLowerCase());
        body.put("opened_at", incident.openedAt().toString());
        body.put("resolved_at", now.toString());
        body.put("resolved_by", by);
        body.put("summary", incident.summary());
        body.put("run_id", incident.runId());
        List<Map<String, Object>> hypotheses = new ArrayList<>();
        for (Hypothesis h : agents.hypotheses(incident.id(), incident.runId())) {
            Map<String, Object> m = new LinkedHashMap<>();
            m.put("rank", h.rank());
            m.put("category", h.category());
            m.put("service", h.service());
            m.put("summary", h.summary());
            m.put("confidence", h.confidence());
            m.put("suggested_fix", h.suggestedFix());
            m.put("verdict", h.verdict());
            m.put("review", h.review() == null ? null : json.readValue(h.review(), Map.class));
            hypotheses.add(m);
        }
        body.put("hypotheses", hypotheses);
        body.put("alerts", incidents.alerts(incident.id()).stream().map(a -> {
            Map<String, Object> m = new LinkedHashMap<>();
            m.put("name", a.name());
            m.put("service", a.service());
            m.put("severity", a.severity());
            m.put("status", a.status());
            m.put("summary", a.summary());
            m.put("starts_at", a.startsAt() == null ? null : a.startsAt().toString());
            m.put("ends_at", a.endsAt() == null ? null : a.endsAt().toString());
            return m;
        }).toList());
        body.put("timeline", incidents.timeline(incident.id()).stream()
                .map(e -> Map.<String, Object>of("at", e.at().toString(), "kind", e.kind(), "message", e.message()))
                .toList());
        List<Map<String, Object>> actions = new ArrayList<>();
        for (Approval a : approvals.forIncident(incident.id())) {
            Map<String, Object> m = new LinkedHashMap<>();
            m.put("title", a.title());
            m.put("status", a.status().name());
            m.put("decided_by", a.decidedBy());
            m.put("decision_reason", a.decisionReason());
            m.put("result", a.result());
            actions.add(m);
        }
        body.put("approvals", actions);
        Map<String, Object> event = new LinkedHashMap<>();
        event.put("event_id", UUID.randomUUID().toString());
        event.put("type", "incident.resolved");
        event.put("at", now.toString());
        event.put("incident", body);
        return json.writeValueAsString(event);
    }

    private Optional<Incident> openIncident(AlertBatch batch, List<AlertItem> firing) {
        AlertItem lead = firing.stream()
                .min(Comparator.comparing(AlertItem::severity).thenComparing(AlertItem::name))
                .orElseThrow();
        Instant openedAt = firing.stream().map(AlertItem::startsAt).filter(Objects::nonNull)
                .min(Comparator.naturalOrder()).orElse(clock.instant());
        String where = lead.service() != null ? lead.service() : batch.namespace();
        String title = lead.name() + " on " + where + (firing.size() > 1 ? " (+" + (firing.size() - 1) + " more)" : "");
        return incidents.insertIfAbsent(UUID.randomUUID(), title, lead.service(), lead.severity(), batch.source(),
                batch.groupKey(), batch.namespace(), openedAt);
    }

    private static String describe(AlertItem alert) {
        String verb = alert.firing() ? " firing" : " resolved";
        String where = alert.service() != null ? " on " + alert.service() : "";
        return alert.name() + where + verb + (alert.summary() != null ? ": " + alert.summary() : "");
    }

    /** The incident.opened contract event (contracts/schemas/incident-opened.schema.json). */
    private String incidentOpened(Incident incident, AlertBatch batch, Instant now) {
        List<Map<String, Object>> alerts = batch.alerts().stream().map(a -> {
            Map<String, Object> alert = new LinkedHashMap<>();
            alert.put("name", a.name());
            alert.put("service", a.service());
            alert.put("severity", a.severity().name());
            alert.put("state", a.firing() ? "firing" : "resolved");
            alert.put("since", a.startsAt() == null ? null : a.startsAt().toString());
            alert.put("summary", a.summary());
            return alert;
        }).toList();
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("id", incident.id().toString());
        body.put("number", incident.number());
        body.put("title", incident.title());
        body.put("opened_at", incident.openedAt().toString());
        body.put("namespace", incident.namespace());
        body.put("alerts", alerts);
        Map<String, Object> event = new LinkedHashMap<>();
        event.put("event_id", UUID.randomUUID().toString());
        event.put("type", "incident.opened");
        event.put("at", now.toString());
        event.put("incident", body);
        return json.writeValueAsString(event);
    }
}
