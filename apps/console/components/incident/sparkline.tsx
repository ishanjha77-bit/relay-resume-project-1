import type { AgentStep } from "@/lib/types"

export interface Series {
  values: number[]
  /** Where the level shifted (index into values), if the metrics tool found a change point. */
  change: number | null
  label: string
}

interface RangeOutput {
  series?: {
    labels?: Record<string, string>
    points?: [string, number | null][]
    change?: { at: string }
  }[]
}

/** The first series of a metrics__query_range result, for a sparkline; null for anything else. */
export function seriesOf(step: AgentStep | undefined): Series | null {
  if (!step?.tool?.endsWith("query_range")) return null
  let parsed: RangeOutput
  try {
    parsed = JSON.parse(String(step.output?.output ?? "")) as RangeOutput
  } catch {
    return null // clipped output
  }
  const first = parsed.series?.find((s) => (s.points?.length ?? 0) > 1)
  if (!first?.points) return null
  const points = first.points.filter((p): p is [string, number] => typeof p[1] === "number")
  const change = first.change ? points.findIndex(([at]) => at >= first.change!.at) : -1
  return {
    values: points.map(([, v]) => v),
    change: change >= 0 ? change : null,
    label: Object.values(first.labels ?? {}).join(" "),
  }
}

/** A mini chart of a metric; the numbers it shows are also in its accessible label. */
export function Sparkline({ series, className }: { series: Series; className?: string }) {
  const { values, change } = series
  const [w, h] = [48, 14]
  const min = Math.min(...values)
  const span = Math.max(...values) - min || 1
  const x = (i: number) => (i / (values.length - 1)) * w
  const y = (v: number) => h - 1 - ((v - min) / span) * (h - 2)
  const last = values.at(-1)!
  return (
    <svg
      viewBox={`0 0 ${w} ${h}`}
      width={w}
      height={h}
      className={className}
      role="img"
      aria-label={`${series.label || "metric"}: from ${values[0]} to ${last}${change !== null ? ", with a level shift" : ""}`}
    >
      {change !== null && (
        <line x1={x(change)} x2={x(change)} y1={0} y2={h} className="stroke-rose-400/60" strokeDasharray="2 1" />
      )}
      <polyline
        points={values.map((v, i) => `${x(i)},${y(v)}`).join(" ")}
        fill="none"
        strokeWidth={1.25}
        strokeLinejoin="round"
        className="stroke-amber-300"
      />
      <circle cx={x(values.length - 1)} cy={y(last)} r={1.5} className="fill-amber-300" />
    </svg>
  )
}
