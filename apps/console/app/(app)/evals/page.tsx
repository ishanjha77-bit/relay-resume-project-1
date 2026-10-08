"use client"

import { useQuery } from "@tanstack/react-query"
import { Check, Minus, X } from "lucide-react"
import Link from "next/link"
import { type ReactNode, useMemo, useState } from "react"
import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
  ZAxis,
} from "recharts"

import { Skeleton } from "@/components/ui/skeleton"
import { api } from "@/lib/api"
import { category, dateTime, duration, percent, usd } from "@/lib/format"
import type { EvalBatch, EvalRun } from "@/lib/types"
import { cn } from "@/lib/utils"

const AXIS = { stroke: "var(--muted-foreground)", fontSize: 11, tickLine: false, axisLine: false }
const TOOLTIP = {
  contentStyle: {
    background: "var(--popover)",
    border: "1px solid var(--border)",
    borderRadius: 8,
    fontSize: 12,
    color: "var(--popover-foreground)",
  },
}

export default function EvalsPage() {
  const batches = useQuery({ queryKey: ["eval-batches"], queryFn: api.evalBatches, refetchInterval: 60_000 })
  const allRuns = useQuery({ queryKey: ["eval-runs"], queryFn: () => api.evalRuns(), refetchInterval: 60_000 })
  const [chosen, setChosen] = useState<string | null>(null)
  const batch = batches.data?.find((b) => b.batch === chosen) ?? batches.data?.[0]
  const runs = useMemo(
    () =>
      (allRuns.data ?? []).filter((r) => r.batch === batch?.batch).sort((a, b) => a.scenario.localeCompare(b.scenario)),
    [allRuns.data, batch],
  )

  if (batches.isPending || allRuns.isPending) {
    return (
      <div className="grid gap-4 p-6 md:grid-cols-4">
        {Array.from({ length: 8 }, (_, i) => (
          <Skeleton key={i} className="h-24" />
        ))}
      </div>
    )
  }
  if (!batch) {
    return (
      <div className="text-muted-foreground p-6 text-sm">
        No eval results yet. Run <code className="bg-muted rounded px-1 font-mono">make eval</code> to score Relay on
        the chaos scenarios.
      </div>
    )
  }

  return (
    <div className="h-full overflow-y-auto">
      <header className="flex flex-wrap items-center gap-3 border-b px-6 py-3">
        <h1 className="text-sm font-semibold">Evals</h1>
        <label className="text-muted-foreground flex items-center gap-2 text-xs">
          Batch
          <select
            value={batch.batch}
            onChange={(e) => setChosen(e.target.value)}
            className="bg-background text-foreground focus-visible:ring-ring rounded-md border px-2 py-1 font-mono text-xs focus-visible:ring-2 focus-visible:outline-none"
          >
            {batches.data?.map((b) => (
              <option key={b.batch} value={b.batch}>
                {b.batch}
              </option>
            ))}
          </select>
        </label>
        <span className="text-muted-foreground text-xs">
          {batch.runs} scenarios · {dateTime(batch.started_at)} – {dateTime(batch.finished_at)} · {models(batch.models)}
        </span>
      </header>

      <div className="space-y-6 p-6">
        <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4" aria-label="Scorecard">
          <Kpi
            label="Root-cause accuracy"
            value={percent(batch.accuracy)}
            hint={`top hypothesis, ${batch.answered} incidents`}
          />
          <Kpi
            label="Accuracy, top 3"
            value={percent(batch.accuracy_top3)}
            hint="right answer anywhere in the ranking"
          />
          <Kpi
            label="Median time to diagnosis"
            value={duration(batch.median_seconds)}
            hint={`fault injected to verdict; agent alone ${duration(batch.median_agent_seconds)}`}
          />
          <Kpi
            label="Cost per incident"
            value={batch.mean_cost_usd ? usd(batch.mean_cost_usd, 3) : "$0"}
            hint={`${batch.mean_steps ?? "n/a"} tool calls, ${batch.mean_llm_calls ?? "n/a"} model calls on average`}
          />
        </section>

        <div className="grid gap-4 xl:grid-cols-2">
          <Panel title="Accuracy and time to diagnosis, by batch">
            <BatchTrend batches={batches.data ?? []} />
          </Panel>
          <Panel title={`Accuracy by fault type · ${batch.batch}`}>
            <ByCategory runs={runs} />
          </Panel>
          <Panel title="Calibration: stated confidence vs. how often it was right">
            <Calibration runs={(allRuns.data ?? []).filter((r) => r.status === "scored")} />
          </Panel>
          <Panel title="Models compared (all batches)">
            <Models runs={allRuns.data ?? []} />
          </Panel>
        </div>

        <Panel title={`Scenarios · ${batch.batch}`}>
          <ScenarioTable runs={runs} />
        </Panel>
      </div>
    </div>
  )
}

/** The models that answered: runs list each model they used, joined with "+". */
function models(list: string | null | undefined): string {
  const names = (list ?? "").split(/[+,]/).map((m) => m.trim())
  return [...new Set(names.filter(Boolean))].sort().join(", ")
}

function Kpi({ label, value, hint }: { label: string; value: string; hint: string }) {
  return (
    <div className="bg-card rounded-lg border p-4">
      <p className="text-muted-foreground text-xs">{label}</p>
      <p className="mt-1 text-2xl font-semibold tracking-tight tabular-nums">{value}</p>
      <p className="text-muted-foreground mt-1 text-[11px]">{hint}</p>
    </div>
  )
}

function Panel({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="bg-card rounded-lg border p-4" aria-label={title}>
      <h2 className="text-muted-foreground mb-3 text-xs font-medium">{title}</h2>
      {children}
    </section>
  )
}

function BatchTrend({ batches }: { batches: EvalBatch[] }) {
  const data = [...batches]
    .reverse()
    .map((b) => ({ batch: b.batch, accuracy: (b.accuracy ?? 0) * 100, minutes: (b.median_seconds ?? 0) / 60 }))
  return (
    <ResponsiveContainer width="100%" height={220}>
      <LineChart data={data} margin={{ top: 8, right: 8, left: -12, bottom: 0 }}>
        <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" vertical={false} />
        <XAxis dataKey="batch" interval={0} padding={{ left: 56, right: 56 }} {...AXIS} />
        <YAxis yAxisId="acc" domain={[0, 100]} unit="%" {...AXIS} />
        <YAxis yAxisId="min" orientation="right" unit="m" {...AXIS} />
        <Tooltip {...TOOLTIP} />
        <Line yAxisId="acc" dataKey="accuracy" name="accuracy %" stroke="var(--chart-1)" strokeWidth={2} dot />
        <Line yAxisId="min" dataKey="minutes" name="median minutes" stroke="var(--chart-2)" strokeWidth={2} dot />
      </LineChart>
    </ResponsiveContainer>
  )
}

function ByCategory({ runs }: { runs: EvalRun[] }) {
  const groups = new Map<string, { right: number; total: number }>()
  for (const r of runs.filter((r) => r.status === "scored" || r.status === "agent_failed")) {
    const g = groups.get(r.category) ?? { right: 0, total: 0 }
    g.total += 1
    g.right += r.correct ? 1 : 0
    groups.set(r.category, g)
  }
  const data = [...groups].map(([name, g]) => ({
    name: category(name),
    accuracy: Math.round((g.right / g.total) * 100),
  }))
  return (
    <ResponsiveContainer width="100%" height={Math.max(220, data.length * 24)}>
      <BarChart data={data} layout="vertical" margin={{ top: 0, right: 8, left: 40, bottom: 0 }}>
        <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" horizontal={false} />
        <XAxis type="number" domain={[0, 100]} unit="%" {...AXIS} />
        <YAxis type="category" dataKey="name" width={130} interval={0} {...AXIS} />
        <Tooltip {...TOOLTIP} />
        <Bar dataKey="accuracy" fill="var(--chart-1)" radius={[0, 4, 4, 0]} />
      </BarChart>
    </ResponsiveContainer>
  )
}

function Calibration({ runs }: { runs: EvalRun[] }) {
  const buckets = [
    [0, 0.5],
    [0.5, 0.7],
    [0.7, 0.85],
    [0.85, 0.95],
    [0.95, 1.01],
  ]
  const data = buckets
    .map(([low, high]) => {
      const inBucket = runs.filter((r) => r.confidence != null && r.confidence >= low && r.confidence < high)
      if (!inBucket.length) return null
      const stated = inBucket.reduce((s, r) => s + (r.confidence ?? 0), 0) / inBucket.length
      const actual = inBucket.filter((r) => r.correct).length / inBucket.length
      return { stated: Math.round(stated * 100), actual: Math.round(actual * 100), runs: inBucket.length }
    })
    .filter(Boolean)
  return (
    <ResponsiveContainer width="100%" height={220}>
      <ScatterChart margin={{ top: 8, right: 8, left: -12, bottom: 0 }}>
        <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" />
        <XAxis type="number" dataKey="stated" name="stated" unit="%" domain={[0, 100]} {...AXIS} />
        <YAxis type="number" dataKey="actual" name="actual" unit="%" domain={[0, 100]} {...AXIS} />
        <ZAxis type="number" dataKey="runs" range={[60, 400]} name="runs" />
        <ReferenceLine
          segment={[
            { x: 0, y: 0 },
            { x: 100, y: 100 },
          ]}
          stroke="var(--muted-foreground)"
          strokeDasharray="4 4"
        />
        <Tooltip {...TOOLTIP} />
        <Scatter data={data} fill="var(--chart-3)" />
      </ScatterChart>
    </ResponsiveContainer>
  )
}

function Models({ runs }: { runs: EvalRun[] }) {
  const groups = new Map<string, { right: number; total: number }>()
  for (const r of runs.filter((r) => r.status === "scored" && r.model)) {
    for (const model of (r.model ?? "").split("+")) {
      const g = groups.get(model) ?? { right: 0, total: 0 }
      g.total += 1
      g.right += r.correct ? 1 : 0
      groups.set(model, g)
    }
  }
  const data = [...groups].map(([model, g]) => ({
    model,
    accuracy: Math.round((g.right / g.total) * 100),
    runs: g.total,
  }))
  return (
    <ResponsiveContainer width="100%" height={220}>
      <BarChart data={data} margin={{ top: 8, right: 8, left: -12, bottom: 0 }}>
        <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" vertical={false} />
        <XAxis dataKey="model" interval={0} angle={-12} textAnchor="end" height={44} {...AXIS} />
        <YAxis domain={[0, 100]} unit="%" {...AXIS} />
        <Tooltip {...TOOLTIP} />
        <Bar dataKey="accuracy" fill="var(--chart-2)" radius={[4, 4, 0, 0]} />
      </BarChart>
    </ResponsiveContainer>
  )
}

function ScenarioTable({ runs }: { runs: EvalRun[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-xs">
        <thead className="text-muted-foreground">
          <tr className="border-b">
            <th className="py-2 pr-3 font-medium">Scenario</th>
            <th className="py-2 pr-3 font-medium">Expected</th>
            <th className="py-2 pr-3 font-medium">Top hypothesis</th>
            <th className="py-2 pr-3 font-medium">Correct</th>
            <th className="py-2 pr-3 text-right font-medium">Confidence</th>
            <th className="py-2 pr-3 text-right font-medium">Tools</th>
            <th className="py-2 pr-3 text-right font-medium">Time</th>
            <th className="py-2 font-medium">Model</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((r) => (
            <tr key={r.id} className="border-b last:border-0">
              <td className="py-2 pr-3 font-mono">
                {r.incident_id ? (
                  <Link href={`/incidents/${r.incident_id}`} className="underline-offset-2 hover:underline">
                    {r.scenario}
                  </Link>
                ) : (
                  r.scenario
                )}
              </td>
              <td className="py-2 pr-3">{category(r.category)}</td>
              <td className="py-2 pr-3">
                {r.top_category ? (
                  `${category(r.top_category)} in ${r.top_service}`
                ) : (
                  <span className="text-muted-foreground">{r.status}</span>
                )}
              </td>
              <td className="py-2 pr-3">
                <Outcome run={r} />
              </td>
              <td className="py-2 pr-3 text-right tabular-nums">{percent(r.confidence)}</td>
              <td className="py-2 pr-3 text-right tabular-nums">{r.steps ?? "–"}</td>
              <td className="py-2 pr-3 text-right tabular-nums">{duration(r.seconds)}</td>
              <td className="text-muted-foreground py-2 font-mono">{r.model ?? "–"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function Outcome({ run }: { run: EvalRun }) {
  const [label, Icon, tone] = run.correct
    ? (["yes", Check, "text-emerald-300"] as const)
    : run.correct_top3
      ? (["top 3", Minus, "text-amber-300"] as const)
      : run.status === "scored" || run.status === "agent_failed"
        ? (["no", X, "text-rose-300"] as const)
        : ([run.status, Minus, "text-muted-foreground"] as const)
  return (
    <span className={cn("inline-flex items-center gap-1", tone)}>
      <Icon className="size-3" aria-hidden />
      {label}
    </span>
  )
}
