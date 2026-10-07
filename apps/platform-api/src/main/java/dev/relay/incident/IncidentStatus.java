package dev.relay.incident;

import java.util.EnumSet;
import java.util.Set;

/**
 * OPEN → INVESTIGATING → DIAGNOSED → (AWAITING_APPROVAL) → RESOLVED. FAILED means
 * the investigation gave up: a human takes over, and the incident stays open
 * (new alerts of its group still attach to it) until someone resolves it.
 */
public enum IncidentStatus {
    OPEN,
    INVESTIGATING,
    DIAGNOSED,
    AWAITING_APPROVAL,
    RESOLVED,
    FAILED;

    public static final Set<IncidentStatus> ACTIVE =
            EnumSet.of(OPEN, INVESTIGATING, DIAGNOSED, AWAITING_APPROVAL, FAILED);

    public boolean active() {
        return ACTIVE.contains(this);
    }
}
