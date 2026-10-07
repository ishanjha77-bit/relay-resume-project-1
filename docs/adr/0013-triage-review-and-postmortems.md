# 13. Triage, review and postmortems: single-purpose agents around the investigator

- Status: accepted
- Date: 2026-10-07

## Context

The first version of Relay had one agent: an investigator running a ReAct
loop over the MCP tools. The baseline eval exposed what one agent leaves
undone:

- **It starts cold.** It spends its first tool calls discovering what the
  alerts resemble, although the team's runbooks already say. Meanwhile the
  on-call engineer sees nothing but "investigating" for one to three minutes.
- **It is overconfident.** It stated 97% confidence on average and was right
  69% of the time. The fixer acts on its top hypothesis, so an unsupported
  diagnosis could become a revert request.
- **It doesn't learn.** A resolved incident leaves nothing behind for the next
  one to find.

A larger, slower model for everything would make the first problem worse and
spend the free tier's quota faster ([ADR 10](0010-free-by-default-gemini.md)).

## Decision

A run is a pipeline of agents, each with one job, its own model route and its
own name in the event stream:

```
triage → investigator → reviewer → fixer ⏸ approval        (one run, one seq)
resolve → postmortem writer → knowledge base                (later, on its own stream)
```

**Triage** (`graph/triage.py`) runs first, on a small model. It searches the
knowledge base twice through the runbooks MCP server, once for runbooks and
once for postmortems. It keeps the best passage of the three most relevant
documents of each kind. It also reads the service graph of the last five
minutes: who called whom, from traces, through the metrics server. From that
it computes the blast radius: the alerting services' callers, transitively
(who feels it), and what they call (where it may come from). One model call
then reads the alerts, the graph and the passages. It returns a severity,
where to start, a one-line summary and at most three leads. The
investigator's brief carries the leads, the failing or slow calls and the
related documents. The console draws the graph as a service map. The triage
decides nothing on its own:
- its severity can raise the incident's, but platform-api never lowers one on
  a model's word;
- it never names a root cause;
- a failed search or model call leaves that part out, and the run goes on
  (90-second limit).

**The reviewer** (`graph/reviewer.py`) reads each hypothesis with the tool
output around every quote it cites, and whether that quote was found. It
judges each one supported, weak or unsupported. It can only lower confidence.
It re-ranks only on its verdicts, so a supported hypothesis moves ahead of a
weak one, and the fixer acts on the reviewed ranking.

**The postmortem writer** (`graph/postmortem.py`) runs when a person resolves
an investigated incident. Platform-api publishes the incident's record
(alerts, hypotheses, timeline, approvals) to `relay.resolved`. The writer
drafts a blameless postmortem: timeline, root cause, impact, action items.
Platform-api stores it and indexes its sections into `knowledge_chunks`, where
the next triage and investigation find it. Eval runs resolve with
`postmortem=false`: a postmortem of one drill would hand later drills the
answer.

**The learning loop.** Responders vote on what the agents showed them. A
related runbook or postmortem is marked helpful or not; a hypothesis is the
root cause or not. Document votes re-rank the knowledge base's search. A
document's fused score is scaled by up to ±30% (net votes capped at ±3), which
is enough to reorder close calls and never enough to bury an exact error-message
match. Hypothesis votes are the labels that real incidents otherwise lack:
accuracy and calibration outside the eval harness need them. Votes are one per
person and thing, can be taken back, and need the RESPONDER role.

**Routing.** Triage and postmortems use the Flash-Lite models first. They are
short, single-call jobs, and the stronger Flash models' daily quota is worth
more to investigations. We tried Gemma (`gemma-4-26b-a4b-it`), whose quota is
separate. It answered the first live triage well apart from garbled characters
in one lead. On the second it fell into a repetition loop: 1,017 output tokens
of "content-content-…" in 23 seconds. Gemma stays out of every route. The
triage schema still folds look-alike Unicode letters (NFKC) and drops any lead
over 400 characters.

**One trace.** All of a run's agents publish to the same `(run_id, seq)`
sequence, each under its own `agent` name. The console shows them as one
trace, and platform-api's idempotency is unchanged. A recording keeps each
model answer with its agent's role, and replays serve each role its own
answers, so a recorded run still replays its investigator alone
(`relay-agent investigate --replay`).

## Consequences

- An investigation costs one more model call, and starts 2–5 s later. In
  return, the on-call engineer sees a severity, a summary and leads within
  seconds, and the investigator starts from the right runbooks.
- Whether triage notes make investigations more accurate or faster is an
  empirical question. The eval batches answer it: the
  `2026-10-06-after` batch ran without triage and without the reviewer.
  `2026-10-07-full` ran with both. Accuracy rose from 76% to 82%, which is
  one verdict in 17, so within noise. Median agent time fell from 93 s to
  78 s, and tool calls from 13.9 to 12.5.
- The reviewer, as measured, made calibration worse: the Brier score went
  from 0.20 to 0.24. Gemini's Flash quota ran out mid-batch, so 12 of its 17
  reviews ran on Flash-Lite. Its 5 Flash reviews all agreed with correct
  diagnoses. Its Flash-Lite reviews cut two correct diagnoses to 10% and 0%
  confidence and passed two wrong ones. One of those cuts came from errors
  left over from the previous scenario, read as an earlier onset; it kept the
  fixer from proposing a correct revert. **Amended (2026-10-07):** the
  reviewer runs on the Flash models only, and is skipped when none is
  available. A check by a weaker model than the investigator's is not a
  check. Whether a Flash reviewer earns its call is measured offline:
  `evals/review_replay.py` replays it over the recorded investigations.
- A model can now raise an incident's severity. It can never lower one, and
  the timeline records who raised it and why.
- Postmortems are drafts written by a model from the incident's record.
  They are shown as such, and they are exported for a person to edit, not
  published.
