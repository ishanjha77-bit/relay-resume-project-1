# 10. Free by default: Gemini's free tier behind the provider interface

- Status: accepted
- Date: 2026-10-05

## Context

Relay should cost nothing to run, for its author and for anyone who clones
it. The agent was built on Claude ([ADR 7](0007-llm-provider-and-agent-loop.md)),
and the Claude API has no free tier. Running a model locally doesn't work
either: the development laptop has no discrete GPU and about 5 GB of RAM free
while the cluster runs, which is far too little for a multi-step agent.

Google's Gemini API has a free tier with no credit card. Its Flash models
allow a few requests per minute and on the order of a thousand per day per
project (exact quotas are shown in AI Studio). An investigation makes 5–15
model calls. Gemini supports function calling with JSON-Schema parameters,
thought summaries, and constrained ("validated") function arguments.

## Decision

**Gemini Flash is the default; Claude is one setting away.** A route names a
model, and its provider follows from the name (`gemini-*`, `claude-*`).
`make llm-check` verifies a key with one tiny request.

**Quota-aware routing across models.** The free tier is small and uneven.
Measured on the first day:
- Each model gets 20 requests a day (`GenerateRequestsPerDayPerProjectPerModel-FreeTier`),
  enough for about two investigations.
- The newest model (3.8 Flash) often answered "503: high demand".
- `gemini-2.5-flash` is closed to new users.

Each model has its own quota, though, so the route is a chain:
`gemini-3.6-flash`, then 3.8 Flash, 3.7 Flash, 3.5 Flash, 3 Flash (preview),
3.5 Flash-Lite and 3.1 Flash-Lite. That is about 140 free requests a day.
(The two extra Flash models were added 2026-10-06, after listing the models
the key can use. `gemini-2.5-flash-lite` is closed to new users too.) **Gemma 4**
(`gemma-4-26b-a4b-it`, an open model on the same API) calls functions as well,
and the adapter supports it: it accepts no thinking level or budget, so it only
gets asked for thought summaries. It is not in the investigator's chain,
though. Its free tier caps input tokens per minute: a 12k-token request
passed, a 25k-token one was refused. Late investigation turns carry 15-30k
tokens, so a run that fell back to Gemma would fail halfway. It suits agents
with small prompts, such as triage or postmortem writing.
- A run continues on the model that wrote its history while that model
  answers.
- When the model is out of its daily quota, overloaded or silent, the next
  model takes over mid-run. Thought signatures are only valid for the model
  that made them, so the earlier turns are re-sent without the old model's
  thoughts. Its function calls carry Gemini's documented "skip validation"
  marker; without it the API returns 400, which we checked against the live API.
- A model out of daily quota is skipped until its reset (the 429 says when).
  A per-minute 429 is waited out on the same model.
- When every model is spent, the run fails with a message saying so and when
  quotas reset. A bad request (4xx) is never passed to another model.

**Native SDK, not an OpenAI-compatible shim.** Gemini 3 binds its reasoning
to function calls with *thought signatures*. A tool-result turn whose
signatures are missing is rejected, and OpenAI's message format has no place
for them. The adapter (`llm/gemini_provider.py`) uses `google-genai`
and keeps the model's raw parts, signatures included, in the stored
conversation. Every later request resends them byte for byte. The
conversation stays Anthropic-shaped, so the investigator, replays and golden
tests are unchanged.

**The report is a function call.** Claude returns the report as structured
output. On Gemini, the report schema becomes a `submit_report` function, and
validated function calling constrains its arguments to the schema. The
adapter hands the arguments back as the same JSON text the investigator
already parses. If the model submits a report and also calls tools in the
same turn, the tool calls are dropped. If the investigator rejects a report,
the next turn first answers each open call, which Gemini requires, then sends
the reminder.

**Other free-tier details.** Thought summaries become the agent's progress
notes in the console. Cost is recorded as $0,
but every token is still counted, so the paid-tier cost can be computed
later.

## Consequences

- Clone, add a free key, `make up`: the whole system, agent included, runs at
  no cost. Cost per incident on the free tier is $0.
- Free-tier prompts may be used by Google to improve its products. That is
  acceptable here because every log, metric and incident is synthetic, from
  the sandbox. A real deployment would use a paid tier or Claude.
- Rate limits cap throughput at a handful of concurrent investigations, which
  is enough for development and for nightly evals of the 17 scenarios.
- Eval results are reported per model. Claude-specific features stay
  implemented for runs on Claude: prompt caching, preserved thinking and
  server-side fallbacks. Comparing providers on the same scenarios becomes
  possible.
