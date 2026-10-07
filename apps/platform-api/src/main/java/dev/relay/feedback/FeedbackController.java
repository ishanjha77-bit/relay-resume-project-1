package dev.relay.feedback;

import java.util.UUID;

import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;

import org.springframework.http.HttpStatus;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

import dev.relay.incident.IncidentRepository;

/**
 * The learning loop's input: a responder marks a related runbook or postmortem as
 * helpful or not, or a hypothesis as the root cause or not. Voting needs the
 * RESPONDER role (SecurityConfig); "none" takes a vote back.
 */
@RestController
@RequestMapping("/api/incidents/{incidentId}/feedback")
class FeedbackController {

    private final FeedbackRepository feedback;
    private final IncidentRepository incidents;

    FeedbackController(FeedbackRepository feedback, IncidentRepository incidents) {
        this.feedback = feedback;
        this.incidents = incidents;
    }

    record HypothesisRef(@NotBlank String runId, @NotBlank String category, @NotBlank String service) {
    }

    /** Exactly one of `document` (a doc_id) and `hypothesis`. */
    record Vote(@Size(max = 200) String document, @Valid HypothesisRef hypothesis,
            @NotNull @Pattern(regexp = "up|down|none") String vote) {
    }

    @PostMapping
    @ResponseStatus(HttpStatus.NO_CONTENT)
    void vote(@PathVariable UUID incidentId, @Valid @RequestBody Vote body, @AuthenticationPrincipal Jwt user) {
        if ((body.document() == null) == (body.hypothesis() == null)) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "vote on a document or on a hypothesis");
        }
        if (incidents.findById(incidentId).isEmpty()) {
            throw new ResponseStatusException(HttpStatus.NOT_FOUND, "no incident " + incidentId);
        }
        int vote = switch (body.vote()) {
            case "up" -> 1;
            case "down" -> -1;
            default -> 0;
        };
        if (body.document() != null) {
            feedback.voteDocument(incidentId, body.document().strip(), user.getSubject(), vote);
        } else {
            HypothesisRef h = body.hypothesis();
            feedback.voteHypothesis(incidentId, h.runId(), h.category(), h.service(), user.getSubject(), vote);
        }
    }
}
