package dev.relay.incident;

import java.util.EnumSet;
import java.util.List;
import java.util.Set;
import java.util.UUID;

import com.fasterxml.jackson.annotation.JsonRawValue;

import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;

import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

import dev.relay.agent.AgentRepository;
import dev.relay.agent.AgentStep;
import dev.relay.agent.Hypothesis;
import dev.relay.approval.Approval;
import dev.relay.approval.ApprovalService;
import dev.relay.feedback.FeedbackRepository;
import dev.relay.feedback.FeedbackRepository.Feedback;
import dev.relay.postmortem.Postmortem;
import dev.relay.postmortem.PostmortemService;

@RestController
@RequestMapping("/api/incidents")
class IncidentController {

    private final IncidentRepository incidents;
    private final IncidentService service;
    private final AgentRepository agents;
    private final ApprovalService approvals;
    private final PostmortemService postmortems;
    private final FeedbackRepository feedback;

    IncidentController(IncidentRepository incidents, IncidentService service, AgentRepository agents,
            ApprovalService approvals, PostmortemService postmortems, FeedbackRepository feedback) {
        this.feedback = feedback;
        this.postmortems = postmortems;
        this.incidents = incidents;
        this.service = service;
        this.agents = agents;
        this.approvals = approvals;
    }

    record IncidentPage(List<IncidentSummary> items, String nextCursor) {
    }

    record IncidentDetail(IncidentSummary incident, String summary, String runId, @JsonRawValue String triage,
            List<IncidentAlert> alerts, List<Hypothesis> hypotheses, List<Approval> approvals, Postmortem postmortem,
            List<TimelineEvent> timeline, Feedback feedback) {
    }

    /** The inbox: newest first, keyset-paginated. status = active | resolved | all. */
    @GetMapping
    IncidentPage list(@RequestParam(defaultValue = "active") String status,
            @RequestParam(required = false) String cursor,
            @RequestParam(defaultValue = "50") @Min(1) @Max(200) int limit) {
        Set<IncidentStatus> statuses = switch (status) {
            case "active" -> IncidentStatus.ACTIVE;
            case "resolved" -> EnumSet.of(IncidentStatus.RESOLVED);
            case "all" -> EnumSet.allOf(IncidentStatus.class);
            default -> throw new IllegalArgumentException("status must be active, resolved or all");
        };
        List<Incident> page = incidents.page(statuses, cursor == null ? null : Cursor.decode(cursor), limit + 1);
        boolean more = page.size() > limit;
        List<Incident> items = more ? page.subList(0, limit) : page;
        return new IncidentPage(items.stream().map(IncidentSummary::from).toList(),
                more ? Cursor.after(items.getLast()).encode() : null);
    }

    @GetMapping("/{id}")
    IncidentDetail get(@PathVariable UUID id, @AuthenticationPrincipal Jwt user) {
        Incident incident = incidents.findById(id).orElseThrow(() -> notFound(id));
        List<Hypothesis> hypotheses = incident.runId() == null ? List.of() : agents.hypotheses(id, incident.runId());
        return new IncidentDetail(IncidentSummary.from(incident), incident.summary(), incident.runId(),
                incidents.triageOf(id).orElse(null), incidents.alerts(id), hypotheses, approvals.forIncident(id),
                postmortems.find(id).orElse(null), incidents.timeline(id),
                feedback.forIncident(id, incident.runId(), user.getSubject()));
    }

    /** The agent's trace for a run (default: the latest), for replay or for catching up before streaming. */
    @GetMapping("/{id}/steps")
    List<AgentStep> steps(@PathVariable UUID id,
            @RequestParam(name = "run_id", required = false) String runId,
            @RequestParam(name = "after_seq", defaultValue = "-1") int afterSeq,
            @RequestParam(defaultValue = "500") @Min(1) @Max(1000) int limit) {
        Incident incident = incidents.findById(id).orElseThrow(() -> notFound(id));
        String run = runId != null ? runId : incident.runId();
        return run == null ? List.of() : agents.steps(id, run, afterSeq, limit);
    }

    @PostMapping("/{id}/resolve")
    ResponseEntity<Void> resolve(@PathVariable UUID id, @AuthenticationPrincipal Jwt user,
            @RequestParam(defaultValue = "true") boolean postmortem) {
        incidents.findById(id).orElseThrow(() -> notFound(id));
        service.resolve(id, user.getSubject(), postmortem);
        return ResponseEntity.noContent().build();
    }

    private static ResponseStatusException notFound(UUID id) {
        return new ResponseStatusException(HttpStatus.NOT_FOUND, "incident " + id + " not found");
    }
}
