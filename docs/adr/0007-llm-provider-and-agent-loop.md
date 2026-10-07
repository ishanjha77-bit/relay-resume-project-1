# 7. LLM provider interface and an append-only agent loop

- Status: accepted
- Date: 2026-10-04

## Context

The investigator is a ReAct loop: the model thinks, calls tools, reads the
results, repeats, and finally answers. Three constraints shape how it talks
to the model:

1. **Cost per incident is a headline metric**, so every call must be priced
   exactly, and the long, repeated prefix (instructions + tool definitions +
   conversation so far) must be served from the prompt cache.
2. **Claude Opus 5.5 binds thinking blocks to the conversation**: if any
   earlier message, the system prompt or the tool list changes between turns,
   the replayed thinking is invalid (a 400, or silently dropped reasoning).
3. **Evaluations must be reproducible** and runnable in CI without a cluster.

## Decision

- **A provider interface** (`LLMProvider.complete(LLMRequest) -> LLMResponse`)
  with Anthropic-format messages as the canonical shape. Implementations:
  `AnthropicProvider` (default), `RecordingProvider` and `ReplayProvider`.
  Gemini / Ollama adapters translate at their edge when added.
- **A manual loop in LangGraph, not the SDK tool runner.** Each LLM call and
  each batch of tool calls is a graph node, so runs are checkpointed between
  steps and can pause for human approval later. The loop also owns things a
  generic runner doesn't: evidence IDs, untrusted-output wrapping, tool and
  cost budgets, role-based tool policy, and per-step trace events.
- **Append-only history.** Assistant turns are stored exactly as returned
  (thinking blocks and signatures included) and never edited; budget notices
  are appended as text after tool results; the tool list is fixed and sorted
  at the start of a run. A test asserts every request's history is a prefix
  of the next one. Requests set `prefix_mismatch_behavior: "error"` so a
  future change that edits history fails loudly.
- **Request shape for Claude Opus 5.5** (`claude-opus-5-5`, the default
  investigator model): adaptive thinking with `display: "updates"` (progress
  notes stream to the console while reasoning stays hidden), explicit
  `effort: "medium"`, the final report as structured output
  (`output_config.format` — forced `tool_choice` is rejected on this model),
  a cache breakpoint on the system prompt plus top-level automatic caching,
  and server-side refusal fallbacks (`fallbacks: "default"`).
- **Model routing by role** via configuration: the investigator on Opus 5.5;
  cheap, high-volume roles (triage, summaries) on a small model (Haiku 4.5)
  when they are added. Prices per model live in `llm/pricing.py`, including
  Opus 5.5's 0.05x cache-read rate.
- **Record / replay.** A live run can record every LLM response and tool
  result; replaying it re-runs the graph with no network and no API cost —
  the basis of golden-trace tests and of A/B comparisons later.

## Consequences

- The system prompt is static (no timestamps, IDs or budgets) so the tools +
  system prefix is shared by every investigation; per-incident data goes in
  the first user message.
- Retroactive context trimming (dropping old tool results to save tokens) is
  off the table on the client side; tool outputs are shaped and capped
  *before* they enter the conversation instead (see ADR 8).
- A fallback to another model on a refusal continues without Opus 5.5's
  thinking — acceptable for a rare path.
