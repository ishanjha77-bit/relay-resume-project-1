"""Evidence handling: IDs, untrusted-data wrapping, injection scanning, citation checks.

Every tool result becomes a numbered piece of evidence. The model sees it
wrapped in a <tool_output> tag marked untrusted; the final report must cite
evidence by ID with verbatim quotes, which are verified mechanically here —
the first line of defence against hallucinated root causes.

Results from the knowledge base (runbooks, postmortems) are reference material:
they say how things usually fail, not what happened in this incident, so a
citation of one never verifies a hypothesis. They are not scanned for injection
either: the runbook about prompt injection quotes attack phrases on purpose.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from relay_agent.graph.schemas import InvestigationReport
from relay_agent.tools.toolbox import SEPARATOR

_INJECTION_PATTERNS = [
    r"ignore (?:all |any )?(?:previous|prior|above|earlier) (?:instructions|prompts|rules)",
    r"disregard (?:all |the |your )?(?:previous|prior|above|earlier)",
    r"\byou are now\b",
    r"\bnew instructions\b",
    r"\bsystem prompt\b",
    r"\bmaintenance mode\b",
    r"\b(?:treat|mark|consider) (?:it|this|the (?:pr|change)) as (?:already )?approved\b",
    r"\bact as (?:an?|the) (?:ai|assistant|agent)\b",
    r"</?tool_output",
    r"\bassistant\s*:",
]
_INJECTION = re.compile("|".join(f"(?:{p})" for p in _INJECTION_PATTERNS), re.IGNORECASE)

MAX_EVIDENCE_CHARS = 16_000
REFERENCE_SERVERS = frozenset({"runbooks"})  # MCP servers whose results are background knowledge


def is_reference(tool: str) -> bool:
    return tool.split(SEPARATOR, 1)[0] in REFERENCE_SERVERS


@dataclass
class Evidence:
    id: str
    tool: str
    arguments: dict[str, Any]
    text: str
    is_error: bool
    latency_ms: int
    injection_markers: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def scan_for_injection(text: str) -> list[str]:
    """Phrases in a tool output that try to instruct an AI. Heuristic, so it only flags."""
    return sorted({m.group(0).lower() for m in _INJECTION.finditer(text)})


def make_evidence(
    index: int, tool: str, arguments: dict[str, Any], text: str, is_error: bool, latency_ms: int
) -> Evidence:
    if len(text) > MAX_EVIDENCE_CHARS:
        text = (
            text[:MAX_EVIDENCE_CHARS]
            + f"\n…[{len(text) - MAX_EVIDENCE_CHARS} chars cut by the agent service]"
        )
    markers = [] if is_reference(tool) else scan_for_injection(text)
    return Evidence(f"E{index}", tool, arguments, text, is_error, latency_ms, markers)


def wrap(evidence: Evidence) -> str:
    """Render a tool result for the model: tagged untrusted (or reference), with no way to close the tag early."""
    body = re.sub(r"</?\s*tool_output", "[tool_output]", evidence.text, flags=re.IGNORECASE)
    trust = "reference" if is_reference(evidence.tool) else "untrusted"
    attrs = f'evidence_id="{evidence.id}" tool="{evidence.tool}" trust="{trust}"'
    if evidence.injection_markers:
        attrs += ' injection_suspected="true"'
    return f"<tool_output {attrs}>\n{body}\n</tool_output>"


@dataclass
class CitationCheck:
    hypothesis: int
    evidence_id: str
    quote: str
    verdict: str  # "verified" | "unknown_evidence" | "quote_not_found" | "reference_not_evidence"


def _canon(text: str) -> str:
    text = text.replace('\\"', '"').replace("\\n", " ").replace("\\t", " ")
    return re.sub(r"\s+", " ", text).strip().lower()


def verify_citations(report: InvestigationReport, evidence: dict[str, Evidence]) -> list[CitationCheck]:
    checks = []
    for i, hypothesis in enumerate(report.hypotheses):
        for citation in hypothesis.evidence:
            source = evidence.get(citation.evidence_id.strip())
            if source is None:
                verdict = "unknown_evidence"
            elif is_reference(source.tool):
                verdict = "reference_not_evidence"
            elif _canon(citation.quote) in _canon(source.text):
                verdict = "verified"
            else:
                verdict = "quote_not_found"
            checks.append(CitationCheck(i, citation.evidence_id, citation.quote, verdict))
    return checks
