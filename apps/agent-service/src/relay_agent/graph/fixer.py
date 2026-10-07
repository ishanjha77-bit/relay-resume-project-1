"""The fixer: from a diagnosis to one reviewable change, executed only with a human's approval.

A small LangGraph graph, checkpointed (in Redis, in the service) so that a run
waiting for an approver survives restarts:

    propose ──(nothing safe to propose)──▶ END
       │
    request: publish approval.requested (what, why, the diff, the risk)
       │
    gate: interrupt() until the platform relays a human's decision
       │
    execute: open the draft pull request with the platform's approval token

What it may propose is deliberately narrow: undo the change that caused the
incident, when the diagnosis is a bad deploy or a bad configuration change and
the deploy repo holds that change. The fixer computes the action mechanically
from the repo; no model writes it, so nothing injected into the investigation
can shape what a human is asked to approve. The approval token binds the
action's exact text, and the MCP server that opens the pull request checks
that binding.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import UTC, datetime
from typing import Any, Protocol, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from relay_agent.events import EventSink
from relay_agent.graph.schemas import Incident, RootCause
from relay_agent.tools.toolbox import Toolbox

log = logging.getLogger(__name__)

# Diagnoses for which undoing a recent change is the fix.
ACTIONABLE = frozenset({RootCause.BAD_DEPLOY.value, RootCause.CONFIG_ERROR.value})
MIN_CONFIDENCE = 0.5
LOOKBACK_MINUTES = 180
# Undoing an undo re-applies a change someone already backed out.
UNDO = re.compile(r"^(revert|rollback|roll back)\b", re.IGNORECASE)


class SequencedSink(EventSink, Protocol):
    seq: int


class FixState(TypedDict, total=False):
    run_id: str
    incident_id: str
    incident_key: str
    opened_at: str
    report: dict[str, Any]
    seq: int  # the run's next event sequence number (the investigation used the ones before)
    proposal: dict[str, Any] | None
    skipped: str
    decision: dict[str, Any]
    outcome: dict[str, Any]


def thread(run_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": f"fix-{run_id}"}}


def reverse_diff(diff: str) -> str:
    """The unified diff of undoing a change: its lines swapped, hunks flipped."""
    out: list[str] = []
    block: list[str] = []

    def flush() -> None:
        out.extend(line for line in block if line.startswith("-"))
        out.extend(line for line in block if line.startswith("+"))
        block.clear()

    for line in diff.splitlines():
        if line.startswith(("diff --git", "--- ", "+++ ")):
            flush()
            out.append(line)
        elif line.startswith("index "):
            flush()
            if m := re.match(r"index (\w+)\.\.(\w+)(.*)", line):
                out.append(f"index {m[2]}..{m[1]}{m[3]}")
        elif m := re.match(r"@@ -(\S+) \+(\S+) @@(.*)", line):
            flush()
            out.append(f"@@ -{m[2]} +{m[1]} @@{m[3]}")
        elif line.startswith("+"):
            block.append("-" + line[1:])
        elif line.startswith("-"):
            block.append("+" + line[1:])
        else:
            flush()
            out.append(line)
    flush()
    return "\n".join(out) + "\n"


def changed_lines(diff: str) -> int:
    return sum(1 for line in diff.splitlines() if line[:1] in "+-" and not line.startswith(("+++", "---")))


def _time(value: str) -> datetime:
    t = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=UTC)


class Fixer:
    def __init__(self, toolbox: Toolbox, sink: SequencedSink, *, also: tuple[EventSink, ...] = ()):
        self.toolbox = toolbox
        self.sink = sink
        self.also = also

    def graph(self, checkpointer: BaseCheckpointSaver):
        g = StateGraph(FixState)
        g.add_node("propose", self.propose)
        g.add_node("request", self.request)
        g.add_node("gate", self.gate)
        g.add_node("execute", self.execute)
        g.add_edge(START, "propose")
        g.add_conditional_edges("propose", lambda s: "request" if s.get("proposal") else END)
        g.add_edge("request", "gate")
        g.add_edge("gate", "execute")
        g.add_edge("execute", END)
        return g.compile(checkpointer=checkpointer)

    async def start(
        self,
        checkpointer: BaseCheckpointSaver,
        incident: Incident,
        run_id: str,
        report: dict[str, Any],
        seq: int,
    ) -> FixState:
        """Propose a fix for a concluded investigation; returns paused at the gate, or done."""
        state: FixState = {
            "run_id": run_id,
            "incident_id": incident.id,
            "incident_key": f"INC-{incident.number}" if incident.number else incident.id,
            "opened_at": incident.opened_at,
            "report": report,
            "seq": seq,
        }
        return await self.graph(checkpointer).ainvoke(state, thread(run_id))

    async def resume(
        self, checkpointer: BaseCheckpointSaver, run_id: str, decision: dict[str, Any]
    ) -> FixState | None:
        """Carry a human's decision into the paused run. None when no run waits for it
        (its state expired, or the decision was already applied)."""
        graph = self.graph(checkpointer)
        snapshot = await graph.aget_state(thread(run_id))
        if "gate" not in snapshot.next:
            return None
        return await graph.ainvoke(Command(resume=decision), thread(run_id))

    async def known(self, checkpointer: BaseCheckpointSaver, run_id: str) -> bool:
        """Whether this store still holds the run's fixer state at all."""
        snapshot = await self.graph(checkpointer).aget_state(thread(run_id))
        return bool(snapshot.values)

    # ------------------------------------------------------------------ nodes
    async def propose(self, state: FixState) -> dict[str, Any]:
        hypotheses = state["report"].get("hypotheses") or []
        if not hypotheses:
            return self._skip("the investigation produced no hypothesis")
        top = hypotheses[0]
        if top.get("category") not in ACTIONABLE:
            return self._skip(f"{top.get('category')} is not fixed by undoing a change")
        if float(top.get("confidence", 0)) < MIN_CONFIDENCE:
            return self._skip(f"confidence {top.get('confidence')} is below {MIN_CONFIDENCE}")
        if not any(s.name == "github__recent_changes" for s in self.toolbox.specs):
            return self._skip("no deploy repository is connected")
        service = top["service"]
        opened = _time(state["opened_at"])
        # Only a change made before the incident can have caused it; the ones after
        # (a rollback by a responder, say) are reactions to it.
        repo, before = await self._changes(service, opened)
        if not before:
            return self._skip(f"no change to {service} in the {LOOKBACK_MINUTES} min before the incident")
        suspect = before[0]
        if UNDO.match(suspect["message"]):
            return self._skip(
                f"the last change to {service} is itself an undo: {suspect['message'].splitlines()[0]}"
            )
        _, latest = await self._changes(service, datetime.now(UTC))
        if latest and latest[0]["sha"] != suspect["sha"]:
            return self._skip(f"{service} changed again after {suspect['sha'][:10]}; nothing left to revert")
        diff_out = await self.toolbox.call("github__change_diff", {"sha": suspect["sha"]})
        diff = json.loads(diff_out.text).get("diff", "") if not diff_out.is_error else ""
        path = (suspect.get("files") or [f"services/{service}.yaml"])[0]
        message = suspect["message"].splitlines()[0]
        title = f'Revert "{message}"'
        evidence = "\n".join(
            f"- {c.get('evidence_id')}: “{c.get('quote')}”" for c in (top.get("evidence") or [])[:3]
        )
        body = (
            f"Relay proposes reverting {suspect['sha'][:10]} for {state['incident_key']}.\n\n"
            f"**Diagnosis** ({top['category']} in {service}, confidence {float(top['confidence']):.0%}): "
            f"{top.get('summary', '')}\n\n"
            + (f"**Evidence**\n{evidence}\n\n" if evidence else "")
            + f"**Change reverted**: {message} ({suspect['time']}, by {suspect['author']})\n\n"
            "Opened by Relay only after a human approved this exact change in the Relay console."
        )
        action = json.dumps(
            {
                "kind": "revert_change",
                "repo": repo,
                "commit": suspect["sha"],
                "path": path,
                "title": title,
                "body": body,
                "incident": state["incident_key"],
            },
            ensure_ascii=False,
        )
        display = reverse_diff(diff) if diff else ""
        return {
            "proposal": {
                "approval_id": str(uuid.uuid4()),
                "kind": "revert_change",
                "title": title,
                "risk": "low" if changed_lines(diff) <= 2 else "medium",
                "rationale": top.get("summary", ""),
                "diff": display,
                "action": action,
            }
        }

    async def request(self, state: FixState) -> dict[str, Any]:
        return {"seq": await self._emit(state, "approval.requested", **state["proposal"])}

    async def gate(self, state: FixState) -> dict[str, Any]:
        decision = interrupt({"approval_id": state["proposal"]["approval_id"]})
        return {"decision": decision}

    async def execute(self, state: FixState) -> dict[str, Any]:
        decision, proposal = state["decision"], state["proposal"]
        approval_id = proposal["approval_id"]
        if decision.get("decision") != "approved":
            return {"outcome": {"status": "rejected", "reason": decision.get("reason")}}
        if decision.get("action") != proposal["action"]:
            # The platform relays the action it showed the approver; it must be ours, byte for byte.
            error = "the approved action differs from the one proposed"
        else:
            out = await self.toolbox.call(
                "github__open_draft_pr",
                {"action": decision["action"], "approval_token": decision.get("token", "")},
            )
            result = json.loads(out.text) if not out.is_error else {"error": out.text}
            if "error" not in result:
                seq = await self._emit(state, "action.executed", approval_id=approval_id, result=result)
                return {"seq": seq, "outcome": {"status": "executed", **result}}
            error = str(result["error"])
        seq = await self._emit(state, "action.failed", approval_id=approval_id, error=error)
        return {"seq": seq, "outcome": {"status": "failed", "error": error}}

    # ---------------------------------------------------------------- helpers
    async def _changes(self, service: str, end: datetime) -> tuple[str, list[dict[str, Any]]]:
        """The deploy repo's name and its changes to the service in the lookback window, newest first."""
        out = await self.toolbox.call(
            "github__recent_changes",
            {"service": service, "minutes": LOOKBACK_MINUTES, "end": end.isoformat().replace("+00:00", "Z")},
        )
        data = {} if out.is_error else json.loads(out.text)
        return data.get("repo", ""), data.get("changes", [])

    async def _emit(self, state: FixState, kind: str, /, **data: Any) -> int:
        # The checkpointed seq, unless the caller set a later one: after a pause, the run
        # has published more (run.finished) than the paused graph knows.
        self.sink.seq = max(state.get("seq", 0), self.sink.seq)
        await self.sink.emit(kind, **data)
        for sink in self.also:
            await sink.emit(kind, **data)
        return self.sink.seq

    @staticmethod
    def _skip(reason: str) -> dict[str, Any]:
        log.info("no fix proposed: %s", reason)
        return {"proposal": None, "skipped": reason}
