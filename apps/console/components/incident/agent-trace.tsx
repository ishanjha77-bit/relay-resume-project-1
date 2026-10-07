"use client"

import {
  AlertTriangle,
  Bot,
  CheckCircle2,
  ChevronRight,
  Cpu,
  Gauge,
  GitPullRequestDraft,
  Hand,
  Play,
  Radar,
  Scale,
  ShieldAlert,
  Sparkles,
  Wrench,
  XCircle,
} from "lucide-react"
import { AnimatePresence, motion } from "motion/react"
import { type ReactNode, useEffect, useState } from "react"

import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import { clock, tokens, usd } from "@/lib/format"
import type { AgentStep } from "@/lib/types"
import { cn } from "@/lib/utils"

/** Every step of the agent's run, as it happens. */
export function AgentTrace({ steps, fresh, live }: { steps: AgentStep[]; fresh: ReadonlySet<number>; live: boolean }) {
  const last = steps.at(-1)
  const running = live && last !== undefined && !["investigation.concluded", "run.failed"].includes(last.kind)
  return (
    <ol className="relative space-y-2" aria-label="Agent trace" aria-live="polite">
      <AnimatePresence initial={false}>
        {steps.map((step) => (
          <motion.li
            key={`${step.run_id}:${step.seq}`}
            initial={fresh.has(step.seq) ? { opacity: 0, y: 6 } : false}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.25 }}
          >
            <Step step={step} fresh={fresh.has(step.seq)} />
          </motion.li>
        ))}
      </AnimatePresence>
      {running && (
        <li className="text-muted-foreground flex items-center gap-2 pl-7 text-xs">
          <span className="relative flex size-2">
            <span className="absolute inline-flex size-full animate-ping rounded-full bg-violet-400 opacity-75" />
            <span className="relative inline-flex size-2 rounded-full bg-violet-400" />
          </span>
          thinking…
        </li>
      )}
    </ol>
  )
}

function Step({ step, fresh }: { step: AgentStep; fresh: boolean }) {
  const out = step.output ?? {}
  switch (step.kind) {
    case "triage.completed": {
      const related = (out.related ?? []) as unknown[]
      return (
        <Row icon={<Radar className="size-3.5 text-violet-300" />} at={step.created_at}>
          <span className="font-medium text-violet-300">Triage</span>
          {out.severity ? (
            <>
              : {String(out.severity)}, start with <Mono>{String(out.service)}</Mono>. {String(out.summary)}
            </>
          ) : (
            <> gave no assessment</>
          )}{" "}
          <span className="text-muted-foreground">
            ({related.length} related runbook{related.length === 1 ? "" : "s"} and postmortems)
          </span>
        </Row>
      )
    }
    case "run.started":
      return (
        <Row icon={<Play className="size-3.5" />} at={step.created_at}>
          Investigation started on <Mono>{String(out.model)}</Mono> · effort {String(out.effort)} · {String(out.tools)}{" "}
          tools · budget {String(out.tool_budget)} calls
        </Row>
      )
    case "agent.progress":
      return (
        <Row icon={<Sparkles className="size-3.5 text-violet-300" />} at={step.created_at}>
          <Thought text={String(out.text ?? "")} animate={fresh} />
        </Row>
      )
    case "llm.completed":
      return (
        <Row icon={<Cpu className="size-3.5" />} at={step.created_at} muted>
          <Mono>{String(out.model)}</Mono> {String(out.stop_reason)} · {tokens(step.tokens_in)} in
          {step.cache_read_tokens ? ` (${tokens(step.cache_read_tokens)} cached)` : ""} · {tokens(step.tokens_out)} out
          · {Number(step.cost_usd ?? 0) > 0 ? usd(Number(step.cost_usd)) : "free"} · {step.latency_ms} ms
        </Row>
      )
    case "tool.called":
      return <ToolCall step={step} />
    case "budget.exhausted":
      return (
        <Row icon={<Gauge className="size-3.5 text-amber-300" />} at={step.created_at}>
          <span className="text-amber-300">{String(out.reason)}</span>
        </Row>
      )
    case "investigation.concluded": {
      const report = (out.report ?? {}) as { summary?: string }
      return (
        <Row icon={<CheckCircle2 className="size-3.5 text-emerald-300" />} at={step.created_at}>
          <span className="font-medium text-emerald-300">Concluded</span> after {String(out.tool_calls)} tool calls and{" "}
          {String(out.llm_calls)} model calls. {report.summary}
        </Row>
      )
    }
    case "run.failed":
      return (
        <Row icon={<XCircle className="size-3.5 text-rose-300" />} at={step.created_at}>
          <span className="text-rose-300">Investigation failed:</span>{" "}
          <span className="break-words">{String(out.error)}</span>
        </Row>
      )
    case "approval.requested":
      return (
        <Row icon={<Hand className="size-3.5 text-amber-300" />} at={step.created_at}>
          <span className="font-medium text-amber-300">Fixer</span> asks a human to approve: {String(out.title)} (risk{" "}
          {String(out.risk)})
        </Row>
      )
    case "action.executed": {
      const result = (out.result ?? {}) as { url?: string; pull_request?: number }
      return (
        <Row icon={<GitPullRequestDraft className="size-3.5 text-emerald-300" />} at={step.created_at}>
          <span className="font-medium text-emerald-300">Fixer</span> opened draft pull request #
          {String(result.pull_request)}
          {result.url && (
            <>
              {" "}
              <Mono>{result.url}</Mono>
            </>
          )}
        </Row>
      )
    }
    case "review.completed": {
      const reviews = (out.reviews ?? []) as { verdict: string; confidence: number; original_confidence: number }[]
      const top = reviews[0]
      return (
        <Row icon={<Scale className="size-3.5 text-sky-300" />} at={step.created_at}>
          <span className="font-medium text-sky-300">Reviewer</span> checked {reviews.length} hypothes
          {reviews.length === 1 ? "is" : "es"} against the evidence
          {top && (
            <>
              : the top one is {top.verdict} (confidence {Math.round(top.original_confidence * 100)}% →{" "}
              {Math.round(top.confidence * 100)}%)
            </>
          )}
        </Row>
      )
    }
    case "run.finished":
      return (
        <Row icon={<CheckCircle2 className="size-3.5" />} at={step.created_at} muted>
          Run finished
        </Row>
      )
    case "action.failed":
      return (
        <Row icon={<XCircle className="size-3.5 text-rose-300" />} at={step.created_at}>
          <span className="text-rose-300">The approved action failed:</span>{" "}
          <span className="break-words">{String(out.error)}</span>
        </Row>
      )
    default:
      return (
        <Row icon={<Bot className="size-3.5" />} at={step.created_at} muted>
          {step.kind}
        </Row>
      )
  }
}

function ToolCall({ step }: { step: AgentStep }) {
  const out = step.output ?? {}
  const error = Boolean(out.is_error)
  const injection = (out.injection_markers as string[] | undefined) ?? []
  const [open, setOpen] = useState(false)
  return (
    <Collapsible open={open} onOpenChange={setOpen} className="bg-card ml-7 rounded-md border">
      <CollapsibleTrigger className="hover:bg-muted/40 focus-visible:ring-ring flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-xs focus-visible:ring-2 focus-visible:outline-none">
        <ChevronRight className={cn("size-3 shrink-0 transition-transform", open && "rotate-90")} aria-hidden />
        <span className="bg-muted rounded px-1 font-mono text-[10px] font-semibold">{step.evidence_id}</span>
        <Wrench className="text-muted-foreground size-3 shrink-0" aria-hidden />
        <span className="font-mono font-medium">{step.tool}</span>
        <span className="text-muted-foreground truncate font-mono">{JSON.stringify(step.input ?? {})}</span>
        <span className="text-muted-foreground ml-auto flex shrink-0 items-center gap-2">
          {injection.length > 0 && (
            <span
              className="inline-flex items-center gap-1 text-rose-300"
              title="Possible prompt injection in this output"
            >
              <ShieldAlert className="size-3" aria-hidden /> injection?
            </span>
          )}
          {error ? (
            <span className="inline-flex items-center gap-1 text-rose-300">
              <AlertTriangle className="size-3" aria-hidden /> error
            </span>
          ) : (
            <span>{tokens(Number(out.chars ?? 0))} chars</span>
          )}
          <span className="tabular-nums">{step.latency_ms} ms</span>
        </span>
      </CollapsibleTrigger>
      <CollapsibleContent>
        <pre className="bg-background/60 text-muted-foreground max-h-80 overflow-auto border-t p-2.5 font-mono text-[11px] leading-relaxed break-words whitespace-pre-wrap">
          {String(out.output ?? "")}
        </pre>
      </CollapsibleContent>
    </Collapsible>
  )
}

function Row({ icon, at, muted, children }: { icon: ReactNode; at: string; muted?: boolean; children: ReactNode }) {
  return (
    <div className={cn("flex gap-2 text-xs leading-relaxed", muted && "text-muted-foreground")}>
      <span className="bg-card text-muted-foreground mt-0.5 grid size-5 shrink-0 place-items-center rounded-full border">
        {icon}
      </span>
      <div className="min-w-0 flex-1">{children}</div>
      <time dateTime={at} className="text-muted-foreground shrink-0 font-mono text-[10px] tabular-nums">
        {clock(at)}
      </time>
    </div>
  )
}

function Mono({ children }: { children: ReactNode }) {
  return <span className="font-mono">{children}</span>
}

/** The agent's progress note; new ones type out with a cursor. */
function Thought({ text, animate }: { text: string; animate: boolean }) {
  const [shown, setShown] = useState(animate ? 0 : text.length)
  useEffect(() => {
    if (shown >= text.length) return
    const timer = setTimeout(() => setShown((n) => Math.min(text.length, n + Math.max(2, text.length / 120))), 12)
    return () => clearTimeout(timer)
  }, [shown, text])
  const visible = text.slice(0, shown)
  return (
    <div className="text-foreground/90 space-y-1">
      {visible.split(/\n{2,}/).map((paragraph, i) => (
        <p key={i} className="whitespace-pre-wrap">
          {renderBold(paragraph)}
        </p>
      ))}
      {shown < text.length && <span className="inline-block h-3 w-1.5 animate-pulse bg-violet-300 align-middle" />}
    </div>
  )
}

/** **heading** markers from thought summaries, as bold text. */
function renderBold(text: string): ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*)/g).map((part, i) =>
    part.startsWith("**") && part.endsWith("**") ? (
      <strong key={i} className="text-foreground font-semibold">
        {part.slice(2, -2)}
      </strong>
    ) : (
      part
    ),
  )
}
