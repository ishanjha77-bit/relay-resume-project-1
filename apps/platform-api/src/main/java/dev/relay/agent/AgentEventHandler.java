package dev.relay.agent;

import java.math.BigDecimal;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.context.ApplicationEventPublisher;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import dev.relay.approval.ApprovalService;
import dev.relay.postmortem.PostmortemService;
import dev.relay.incident.IncidentChanged;
import dev.relay.incident.IncidentRepository;
import dev.relay.incident.Severity;
import tools.jackson.databind.json.JsonMapper;

/**
 * Persists one agent event and applies its effect on the incident. Idempotent:
 * a redelivered event finds its (run_id, seq) already stored and changes nothing.
 */
@Service
class AgentEventHandler {

    private static final Logger log = LoggerFactory.getLogger(AgentEventHandler.class);

    private final AgentRepository agents;
    private final IncidentRepository incidents;
    private final JsonMapper json;
    private final ApplicationEventPublisher events;
    private final ApprovalService approvals;
    private final PostmortemService postmortems;

    AgentEventHandler(AgentRepository agents, IncidentRepository incidents, JsonMapper json,
            ApplicationEventPublisher events, ApprovalService approvals, PostmortemService postmortems) {
        this.postmortems = postmortems;
        this.agents = agents;
        this.incidents = incidents;
        this.json = json;
        this.events = events;
        this.approvals = approvals;
    }

    @Transactional
    public void handle(AgentEvent event, String agent) {
        if (incidents.findById(event.incidentId()).isEmpty()) {
            log.warn("Ignoring {} for unknown incident {}", event.type(), event.incidentId());
            return;
        }
        AgentStep step = toStep(event, agent);
        if (!agents.insertStep(event.incidentId(), step)) {
            log.debug("Duplicate event {} seq {} ignored", event.runId(), event.seq());
            return;
        }
        boolean incidentChanged = switch (event.type()) {
            case "triage.completed" -> {
                triaged(event);
                yield true;
            }
            case "run.started" -> {
                incidents.startRun(event.incidentId(), event.runId());
                timeline(event, "agent.started", "Investigation started: %s, effort %s, budget %s tool calls"
                        .formatted(event.string("model"), event.string("effort"), event.integer("tool_budget")));
                yield true;
            }
            case "llm.completed" -> {
                Double cost = event.number("cost_usd");
                if (cost != null && cost > 0) {
                    incidents.addCost(event.incidentId(), BigDecimal.valueOf(cost));
                }
                yield cost != null && cost > 0;
            }
            case "investigation.concluded" -> {
                concluded(event);
                yield true;
            }
            case "postmortem.written" -> {
                postmortems.written(event.incidentId(), event.runId(), event.at(), event.data());
                yield true;
            }
            case "review.completed" -> {
                reviewed(event);
                yield true;
            }
            case "approval.requested" -> approvals.requested(event.incidentId(), event.runId(), agent, event.at(),
                    event.data());
            case "action.executed" -> approvals.finished(event.incidentId(),
                    UUID.fromString(event.string("approval_id")), event.at(), event.object("result"), null);
            case "action.failed" -> approvals.finished(event.incidentId(),
                    UUID.fromString(event.string("approval_id")), event.at(), Map.of(), event.string("error"));
            case "run.failed" -> {
                incidents.attachRun(event.incidentId(), event.runId());
                incidents.fail(event.incidentId());
                timeline(event, "agent.failed", "Investigation failed: " + event.string("error"));
                yield true;
            }
            default -> false;
        };
        events.publishEvent(new StepRecorded(event.incidentId(), step));
        if (incidentChanged) {
            events.publishEvent(new IncidentChanged(event.incidentId()));
        }
    }

    private void concluded(AgentEvent event) {
        Map<String, Object> report = event.object("report");
        List<Map<String, Object>> checks = AgentEvent.objects(event.data().get("checks"));
        List<Hypothesis> hypotheses = new ArrayList<>();
        List<Map<String, Object>> raw = AgentEvent.objects(report.get("hypotheses"));
        for (int rank = 0; rank < raw.size(); rank++) {
            Map<String, Object> h = raw.get(rank);
            hypotheses.add(new Hypothesis(event.runId(), rank, str(h, "category"), str(h, "service"),
                    str(h, "component"), str(h, "summary"),
                    BigDecimal.valueOf(((Number) h.getOrDefault("confidence", 0)).doubleValue()),
                    json.writeValueAsString(h.getOrDefault("evidence", List.of())), str(h, "suggested_fix"),
                    verdict(checks, rank), null, null));
        }
        agents.replaceHypotheses(event.incidentId(), event.runId(), hypotheses);
        if (!hypotheses.isEmpty()) {
            Hypothesis top = hypotheses.getFirst();
            incidents.diagnose(event.incidentId(), str(report, "summary"), top.category(), top.service());
            timeline(event, "agent.diagnosed", "Root cause: %s in %s (confidence %.2f)"
                    .formatted(top.category(), top.service(), top.confidence()));
        }
        if (Boolean.TRUE.equals(report.get("injection_suspected"))) {
            timeline(event, "security.injection", "Prompt injection suspected in tool output "
                    + report.getOrDefault("injection_evidence_ids", List.of()));
        }
    }

    /**
     * The triage agent's first look. Its severity can raise the incident's, never lower it:
     * a model must not talk a page down.
     */
    private void triaged(AgentEvent event) {
        Map<String, Object> triage = new LinkedHashMap<>(event.data());
        triage.put("run_id", event.runId());
        incidents.triage(event.incidentId(), json.writeValueAsString(triage));
        String severity = str(triage, "severity");
        if (severity == null) {
            timeline(event, "agent.triaged", "Triage found %d related runbooks and postmortems; no assessment"
                    .formatted(AgentEvent.objects(triage.get("related")).size()));
            return;
        }
        Severity assessed = Severity.parse(severity);
        Severity current = incidents.findById(event.incidentId()).orElseThrow().severity();
        String raised = "";
        if (assessed.moreSevereThan(current)) {
            incidents.escalate(event.incidentId(), assessed);
            raised = " (raised from %s)".formatted(current);
        }
        timeline(event, "agent.triaged", "Triage: %s%s; start with %s. %s"
                .formatted(assessed, raised, str(triage, "service"), str(triage, "summary")));
    }

    /**
     * The reviewer's verdicts: each hypothesis gets its verdict and (lowered) confidence,
     * the ranking follows the reviewer's order, and the incident's root cause follows a
     * new top hypothesis.
     */
    private void reviewed(AgentEvent event) {
        List<Hypothesis> current = agents.hypotheses(event.incidentId(), event.runId());
        if (current.isEmpty()) {
            return;
        }
        Map<Integer, Hypothesis> byRank = new LinkedHashMap<>();
        current.forEach(h -> byRank.put(h.rank(), h));
        List<Hypothesis> reviewed = new ArrayList<>();
        for (Map<String, Object> r : AgentEvent.objects(event.data().get("reviews"))) {
            Hypothesis h = byRank.remove(((Number) r.get("rank")).intValue());
            if (h == null) {
                continue;
            }
            Map<String, Object> review = new LinkedHashMap<>();
            review.put("verdict", r.get("verdict"));
            review.put("reason", r.get("reason"));
            review.put("model", event.string("model"));
            reviewed.add(new Hypothesis(h.runId(), reviewed.size(), h.category(), h.service(), h.component(),
                    h.summary(), BigDecimal.valueOf(((Number) r.get("confidence")).doubleValue()), h.evidence(),
                    h.suggestedFix(), h.verdict(), h.confidence(), json.writeValueAsString(review)));
        }
        for (Hypothesis h : byRank.values()) { // not reviewed: kept, after the reviewed ones
            reviewed.add(new Hypothesis(h.runId(), reviewed.size(), h.category(), h.service(), h.component(),
                    h.summary(), h.confidence(), h.evidence(), h.suggestedFix(), h.verdict(), null, null));
        }
        agents.replaceHypotheses(event.incidentId(), event.runId(), reviewed);
        Hypothesis before = current.getFirst();
        Hypothesis top = reviewed.getFirst();
        String verdict = String.valueOf(AgentEvent.objects(event.data().get("reviews")).getFirst().get("verdict"));
        if (!top.category().equals(before.category()) || !top.service().equals(before.service())) {
            incidents.rerank(event.incidentId(), top.category(), top.service());
            timeline(event, "agent.reviewed", "Reviewer re-ranked: %s in %s is now the top hypothesis (%s); %s in %s was"
                    .formatted(top.category(), top.service(), verdict, before.category(), before.service()));
        } else {
            timeline(event, "agent.reviewed", "Reviewer: the top hypothesis is %s (confidence %.2f -> %.2f)"
                    .formatted(verdict, before.confidence(), top.confidence()));
        }
    }

    /** "verified" when every citation of the hypothesis matched its evidence verbatim. */
    private static String verdict(List<Map<String, Object>> checks, int rank) {
        List<Map<String, Object>> mine = checks.stream()
                .filter(c -> c.get("hypothesis") instanceof Number n && n.intValue() == rank)
                .toList();
        if (mine.isEmpty()) {
            return "unverified";
        }
        long failed = mine.stream().filter(c -> !"verified".equals(c.get("verdict"))).count();
        return failed == 0 ? "verified" : failed + " of " + mine.size() + " citations unverified";
    }

    private AgentStep toStep(AgentEvent event, String agent) {
        Map<String, Object> data = event.data();
        String input = null;
        String tool = null;
        String evidenceId = null;
        Map<String, Object> output = data;
        if ("tool.called".equals(event.type())) {
            tool = event.string("tool");
            evidenceId = event.string("evidence_id");
            input = json.writeValueAsString(data.getOrDefault("arguments", Map.of()));
            output = new LinkedHashMap<>(data);
            output.remove("arguments");
        }
        boolean llm = "llm.completed".equals(event.type());
        return new AgentStep(
                event.runId(),
                event.seq(),
                agent,
                event.type(),
                tool,
                evidenceId,
                input,
                json.writeValueAsString(output),
                llm ? event.integer("prompt_tokens") : null,
                llm ? event.integer("output_tokens") : null,
                llm ? event.integer("cache_read_tokens") : null,
                llm ? AgentRepository.decimal(event.number("cost_usd")) : null,
                event.integer("latency_ms"),
                event.at());
    }

    private void timeline(AgentEvent event, String kind, String message) {
        incidents.addEvent(event.incidentId(), event.at(), kind, message,
                json.writeValueAsString(Map.of("run_id", event.runId(), "seq", event.seq())));
    }

    private static String str(Map<String, Object> map, String key) {
        Object value = map.get(key);
        return value == null ? null : value.toString();
    }
}
