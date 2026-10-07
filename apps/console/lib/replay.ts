"use client"

import { useEffect, useState } from "react"

import type { AgentStep } from "@/lib/types"

/** How long each kind of step stays on screen while a trace replays. */
function pause(step: AgentStep): number {
  if (step.kind === "agent.progress") return 1400
  if (step.kind === "tool.called") return 550
  return 300
}

/**
 * Replays a recorded trace in the browser, step by step. While it plays, `steps` grows
 * one step at a time and `fresh` holds the newest, so the trace animates as it did live.
 */
export function useReplay(trace: AgentStep[]) {
  const [shown, setShown] = useState<number | null>(null)
  useEffect(() => {
    if (shown === null) return
    const timer = setTimeout(
      () => setShown((n) => (n !== null && n < trace.length ? n + 1 : null)),
      pause(trace[Math.min(shown, trace.length) - 1] ?? trace[0]),
    )
    return () => clearTimeout(timer)
  }, [shown, trace])
  const playing = shown !== null
  return {
    playing,
    steps: playing ? trace.slice(0, shown) : trace,
    fresh: new Set<number>(playing && shown > 0 ? [trace[shown - 1].seq] : []),
    start: () => setShown(1),
    stop: () => setShown(null),
  }
}
