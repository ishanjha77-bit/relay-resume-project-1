"use client"

import { useId, useMemo, useState } from "react"

import type { DependencyEdge } from "@/lib/types"
import { cn } from "@/lib/utils"

const WIDTH = 320
const NODE_W = 58
const NODE_H = 20
const ROW = 34

/** "users" for the service graph's virtual caller: traffic from outside. */
const label = (node: string) => (node === "user" ? "users" : node)
const pct = (ratio: number | null) => `${Math.round((ratio ?? 0) * 100)}%`

function describe(e: DependencyEdge): string {
  return `${label(e.from)} → ${label(e.to)}: ${e.rps ?? "?"} req/s, ${pct(e.failed_ratio)} failed${e.p95_s != null ? `, p95 ${e.p95_s} s` : ""}`
}

function tone(e: DependencyEdge): "failing" | "slow" | "ok" {
  if ((e.failed_ratio ?? 0) >= 0.05) return "failing"
  if ((e.failed_ratio ?? 0) >= 0.01 || (e.p95_s ?? 0) >= 1) return "slow"
  return "ok"
}

/** Columns by call depth: callers left, what they call right (longest path from the entry points). */
function layout(edges: DependencyEdge[]) {
  const nodes = [...new Set(edges.flatMap((e) => [e.from, e.to]))].sort()
  const depth = new Map(nodes.map((n) => [n, 0]))
  for (let i = 0; i < nodes.length; i++) {
    let changed = false
    for (const e of edges) {
      const next = (depth.get(e.from) ?? 0) + 1
      if (next > (depth.get(e.to) ?? 0) && next < nodes.length) {
        depth.set(e.to, next)
        changed = true
      }
    }
    if (!changed) break
  }
  const columns = Math.max(...depth.values()) + 1
  const byColumn = Array.from({ length: columns }, (_, c) => nodes.filter((n) => depth.get(n) === c))
  const rows = Math.max(...byColumn.map((c) => c.length))
  const height = rows * ROW + 8
  const step = columns > 1 ? (WIDTH - NODE_W) / (columns - 1) : 0
  const at = new Map<string, { x: number; y: number }>()
  byColumn.forEach((column, c) =>
    column.forEach((n, r) => at.set(n, { x: c * step, y: (height / (column.length + 1)) * (r + 1) - NODE_H / 2 })),
  )
  return { nodes, at, height }
}

/**
 * Who called whom while the incident began, from traces: the alerting services ringed,
 * failing calls labelled. Focus or hover a service to see its calls.
 */
export function ServiceMap({ edges, alerting }: { edges: DependencyEdge[]; alerting: ReadonlySet<string> }) {
  const arrow = useId()
  const [focus, setFocus] = useState<string | null>(null)
  const { nodes, at, height } = useMemo(() => layout(edges), [edges])
  const shown = focus ? edges.filter((e) => e.from === focus || e.to === focus) : edges.filter((e) => tone(e) !== "ok")

  return (
    <figure className="mt-3">
      <svg
        viewBox={`-2 0 ${WIDTH + 4} ${height}`}
        className="w-full"
        role="group"
        aria-label="Service map: who called whom in the 5 minutes before triage"
      >
        <defs>
          <marker id={arrow} viewBox="0 0 6 6" refX="5" refY="3" markerWidth="5" markerHeight="5" orient="auto">
            <path d="M0,0 L6,3 L0,6 z" className="fill-muted-foreground" />
          </marker>
        </defs>
        {edges.map((e) => {
          const a = at.get(e.from)
          const b = at.get(e.to)
          if (!a || !b) return null
          const [x1, y1, x2, y2] = [a.x + NODE_W, a.y + NODE_H / 2, b.x, b.y + NODE_H / 2]
          const mid = (x1 + x2) / 2
          const t = tone(e)
          const lit = focus === null || e.from === focus || e.to === focus
          return (
            <g key={`${e.from}->${e.to}`} className={cn("transition-opacity", !lit && "opacity-20")}>
              <path
                d={`M${x1},${y1} C${mid},${y1} ${mid},${y2} ${x2 - 1},${y2}`}
                fill="none"
                markerEnd={`url(#${arrow})`}
                strokeWidth={t === "ok" ? 1 : 1.75}
                strokeDasharray={t === "failing" ? "4 2" : undefined}
                className={cn(
                  t === "failing"
                    ? "stroke-rose-400"
                    : t === "slow"
                      ? "stroke-amber-400"
                      : "stroke-muted-foreground/60",
                )}
              >
                <title>{describe(e)}</title>
              </path>
              {t !== "ok" && (
                <text
                  x={mid}
                  y={(y1 + y2) / 2 - 3}
                  textAnchor="middle"
                  className={cn("text-[9px]", t === "failing" ? "fill-rose-300" : "fill-amber-300")}
                >
                  {(e.failed_ratio ?? 0) >= 0.01 ? pct(e.failed_ratio) : `${e.p95_s}s`}
                </text>
              )}
            </g>
          )
        })}
        {nodes.map((n) => {
          const p = at.get(n)!
          const hot = alerting.has(n)
          return (
            <g
              key={n}
              transform={`translate(${p.x},${p.y})`}
              tabIndex={0}
              role="button"
              aria-pressed={focus === n}
              aria-label={`${label(n)}${hot ? ", alerting" : ""}`}
              onMouseEnter={() => setFocus(n)}
              onMouseLeave={() => setFocus(null)}
              onFocus={() => setFocus(n)}
              onBlur={() => setFocus(null)}
              className="cursor-default outline-none [&:focus-visible>rect]:stroke-[var(--ring)] [&:focus-visible>rect]:stroke-2"
            >
              <rect
                width={NODE_W}
                height={NODE_H}
                rx={4}
                className={cn("fill-card", hot ? "stroke-rose-400" : "stroke-border")}
                strokeWidth={hot ? 1.5 : 1}
              />
              <text
                x={NODE_W / 2}
                y={NODE_H / 2 + 3.5}
                textAnchor="middle"
                className="fill-foreground font-mono text-[10px]"
              >
                {label(n)}
              </text>
            </g>
          )
        })}
      </svg>
      <figcaption className="text-muted-foreground mt-1 space-y-0.5 text-[11px]" aria-live="polite">
        {shown.length > 0 ? (
          shown.map((e) => <p key={`${e.from}->${e.to}`}>{describe(e)}</p>)
        ) : (
          <p>No failing or slow calls.</p>
        )}
      </figcaption>
    </figure>
  )
}
