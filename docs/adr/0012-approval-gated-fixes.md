# 12. Approval-gated fixes: a narrow action space, a signed approval, a durable pause

- Status: accepted
- Date: 2026-10-06

## Context

A diagnosis is only half the job: when the cause is a bad deploy or a bad
configuration change, the fix is usually to undo that change. Relay should
propose that fix, but an agent must never change production on its own. Its
inputs include text an attacker can write (log lines, error bodies; see
[ADR 8](0008-mcp-tool-servers.md)), and its judgement is measurably
overconfident: the baseline eval found 97% stated confidence against 69%
accuracy. Every write must wait for a human, and the human must be approving
the exact thing that will run.

The sandbox needs somewhere for such a fix to land. Real teams roll out
through a deploy repository (GitOps): a rollout is a commit, and so is its
fix.

## Decision

**The deploy repository.** `shop/deploy` holds one file per sandbox service:
its image and environment. CD commits every rollout there, with a message
saying why. The chaos scripts do that too (`record_rollout` in
`sandbox/chaos/lib.sh`, through `scripts/deploy_repo.py`). By default it lives
on a Gitea in the cluster, so everything works free and offline. Setting
`deployRepo.host=github` points the same MCP server at a real GitHub
repository. Two bots, least privilege: `ci-bot` may push to `main`; `relay-bot`
may only push branches and open pull requests. `main` is protected against
it, and a merge needs an approval it can't give itself.

**A narrow, mechanical action space.** The fixer (`graph/fixer.py`) proposes
exactly one kind of action: revert the commit that last changed the guilty
service's file. It only does so when:
- the top hypothesis is `bad_deploy` or `config_error`, with confidence ≥ 0.5;
- that commit was made before the incident opened;
- nothing has changed the file since;
- the commit isn't itself a rollback or revert.

The action and its diff are computed from the repository; no model writes
them. Nothing injected into the investigation can shape what a human is asked
to approve.

**A signed approval bound to the action.** The agent publishes
`approval.requested` with the action's exact JSON text. The platform stores it
byte for byte and shows it, with the diff, the rationale and the risk, on the
incident page. Only a user with the APPROVER role can decide; a rejection needs
a reason. An approval produces a short-lived RS256 JWT, signed with the
platform's key: audience `relay-actions`, subject the approval, and
`action_sha256` the SHA-256 of that exact text. The decision goes to the agent
service through the outbox (`relay.approvals`,
`contracts/schemas/approval-decision.schema.json`). The API refuses these
tokens as logins.

**Verification where the write happens.** `open_draft_pr` in the github MCP
server checks the token itself against the platform's JWKS: signature, issuer,
audience, expiry, and the hash of the action it was asked to run. It also
refuses an action for another repository, and a revert that would undo newer
work. It opens a draft pull request on a branch named after the approval, and
merges nothing. A retry finds the existing pull request instead of opening a
second one. Even a fully compromised agent therefore can't open a pull request
that no human approved, or swap the action after approval.

**A durable pause.** The fixer is a LangGraph graph: propose → request
(publish the approval request) → gate (`interrupt()`) → execute. Checkpoints
live in Relay's Redis (langgraph-checkpoint-redis, 7-day TTL), so a run
waiting for a human survives restarts of the agent service. While it waits, the
run holds no slot. The decision resumes it with `Command(resume=...)`, in
whichever replica reads the message. A decision for a run whose state has
expired is reported as `action.failed`, not executed.

## Consequences

- The incident flow gains a status that was already in the model:
  AWAITING_APPROVAL. The eval runner and `make smoke` treat it as a verdict.
- Only reverts of recorded changes can be proposed. Code fixes, scaling and
  restarts stay suggestions in the report, for now.
- One more pod (Gitea, about 150 MiB) and one more MCP server. The agent
  service depends on Redis's search and JSON modules (Redis 8 ships both).
- In eval batches the sandbox is reset as soon as an incident opens. That reset
  is a rollback commit, so the fixer correctly declines to propose anything:
  the change is already undone.
