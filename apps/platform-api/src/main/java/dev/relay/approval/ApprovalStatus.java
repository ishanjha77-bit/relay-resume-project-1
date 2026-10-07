package dev.relay.approval;

/** PENDING → APPROVED | REJECTED (a human decides, once); APPROVED → EXECUTED | FAILED (the agent reports). */
public enum ApprovalStatus {
    PENDING,
    APPROVED,
    REJECTED,
    EXECUTED,
    FAILED
}
