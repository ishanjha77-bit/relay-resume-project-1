package dev.relay.approval;

import java.util.List;
import java.util.UUID;

import jakarta.validation.Valid;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;

import org.springframework.http.HttpStatus;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

/** Approve or reject what an agent wants to do. Deciding needs the APPROVER role (SecurityConfig). */
@RestController
@RequestMapping("/api/incidents/{incidentId}/approvals")
class ApprovalController {

    private final ApprovalService service;

    ApprovalController(ApprovalService service) {
        this.service = service;
    }

    /** @param reason required to reject, so the agent (and the audit trail) learn why */
    record Decision(@NotNull @Pattern(regexp = "approve|reject") String decision, @Size(max = 2000) String reason) {
    }

    @GetMapping
    List<Approval> list(@PathVariable UUID incidentId) {
        return service.forIncident(incidentId);
    }

    @PostMapping("/{approvalId}")
    Approval decide(@PathVariable UUID incidentId, @PathVariable UUID approvalId, @Valid @RequestBody Decision body,
            @AuthenticationPrincipal Jwt user) {
        boolean approve = body.decision().equals("approve");
        String reason = body.reason() == null ? null : body.reason().strip();
        if (!approve && (reason == null || reason.isEmpty())) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "a rejection needs a reason");
        }
        return service.decide(incidentId, approvalId, approve, reason, user.getSubject());
    }
}
