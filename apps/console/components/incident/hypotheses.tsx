"use client"

import { CheckCircle2, CircleAlert, Scale, Wrench } from "lucide-react"
import { AnimatePresence, motion } from "motion/react"

import { category } from "@/lib/format"
import type { AgentStep, Citation, Hypothesis, Vote, Votes } from "@/lib/types"
import { cn } from "@/lib/utils"

import { seriesOf, Sparkline } from "./sparkline"
import { VoteButtons } from "./vote-buttons"

/** Root-cause hypotheses ranked by confidence; evidence chips open the cited output, and a
 * chip citing a metric over time carries a mini chart of it. */
export function Hypotheses({
  hypotheses,
  onEvidence,
  evidence = new Map(),
  votes = {},
  onVote,
  canVote = false,
}: {
  hypotheses: Hypothesis[]
  onEvidence: (citation: Citation) => void
  evidence?: ReadonlyMap<string, AgentStep>
  /** Responders' votes, by "category:service": labels for accuracy on real incidents. */
  votes?: Record<string, Votes>
  onVote?: (hypothesis: Hypothesis, vote: Vote) => void
  canVote?: boolean
}) {
  const ranked = [...hypotheses].sort((a, b) => a.rank - b.rank)
  return (
    <ol className="space-y-3" aria-label="Root-cause hypotheses, most likely first">
      <AnimatePresence initial={false}>
        {ranked.map((h) => (
          <motion.li
            key={`${h.category}:${h.service}`}
            layout
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            transition={{ type: "spring", stiffness: 380, damping: 32 }}
            className={cn(
              "bg-card rounded-lg border p-3",
              h.rank === 0 && "border-amber-500/40 shadow-[0_0_0_1px] shadow-amber-500/10",
            )}
          >
            <div className="flex items-start gap-2">
              <span className="text-muted-foreground mt-0.5 font-mono text-xs">#{h.rank + 1}</span>
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium">{category(h.category)}</p>
                <p className="text-muted-foreground truncate font-mono text-xs">
                  {h.service} · {h.component}
                </p>
              </div>
              <Verdict verdict={h.verdict} />
            </div>
            <Confidence value={h.confidence} original={h.original_confidence} />
            {h.review && <Review review={h.review} />}
            <p className="text-muted-foreground mt-2 text-xs leading-relaxed">{h.summary}</p>
            {h.evidence.length > 0 && (
              <ul className="mt-2 flex flex-col gap-1.5" aria-label="Evidence">
                {h.evidence.map((c, i) => {
                  const series = seriesOf(evidence.get(c.evidence_id))
                  return (
                    <li key={`${c.evidence_id}-${i}`} className="min-w-0">
                      <button
                        type="button"
                        onClick={() => onEvidence(c)}
                        title={c.shows}
                        className="bg-muted/40 hover:bg-muted focus-visible:ring-ring flex w-full min-w-0 items-center gap-1 overflow-hidden rounded-md border px-1.5 py-0.5 text-left text-[11px] focus-visible:ring-2 focus-visible:outline-none"
                      >
                        <span className="font-mono font-semibold">{c.evidence_id}</span>
                        <span className="text-muted-foreground truncate">“{c.quote}”</span>
                        {series && <Sparkline series={series} className="ml-auto shrink-0" />}
                      </button>
                    </li>
                  )
                })}
              </ul>
            )}
            {h.suggested_fix && (
              <p className="bg-muted/40 mt-2 flex gap-1.5 rounded-md px-2 py-1.5 text-xs">
                <Wrench className="text-muted-foreground mt-0.5 size-3 shrink-0" aria-hidden />
                <span>{h.suggested_fix}</span>
              </p>
            )}
            {onVote && (
              <div className="text-muted-foreground mt-2 flex items-center justify-end gap-1 text-[10px]">
                Root cause?
                <VoteButtons
                  votes={votes[`${h.category}:${h.service}`]}
                  onVote={(vote) => onVote(h, vote)}
                  disabled={!canVote}
                  up="the root cause"
                  down="not the root cause"
                  label={`Is ${category(h.category)} in ${h.service} the root cause?`}
                />
              </div>
            )}
          </motion.li>
        ))}
      </AnimatePresence>
    </ol>
  )
}

function Confidence({ value, original }: { value: number; original?: number | null }) {
  const pct = Math.round(value * 100)
  const before = original != null && Math.round(original * 100) !== pct ? Math.round(original * 100) : null
  return (
    <div
      className="mt-2 flex items-center gap-2"
      aria-label={
        before === null ? `Confidence ${pct}%` : `Confidence ${pct}%, lowered by the reviewer from ${before}%`
      }
    >
      <div className="bg-muted h-1.5 flex-1 overflow-hidden rounded-full">
        <motion.div
          className="h-full rounded-full bg-amber-400"
          initial={{ width: 0 }}
          animate={{ width: `${pct}%` }}
          transition={{ duration: 0.6, ease: "easeOut" }}
        />
      </div>
      {before !== null && (
        <span className="text-muted-foreground font-mono text-[10px] tabular-nums line-through">{before}%</span>
      )}
      <span className="w-9 text-right font-mono text-xs tabular-nums">{pct}%</span>
    </div>
  )
}

const REVIEW_TONE = {
  supported: "border-emerald-500/30 text-emerald-300",
  weak: "border-amber-500/30 text-amber-300",
  unsupported: "border-rose-500/30 text-rose-300",
} as const

/** The reviewer agent's check of this hypothesis against its evidence. */
function Review({ review }: { review: NonNullable<Hypothesis["review"]> }) {
  return (
    <p className="mt-2 flex items-start gap-1.5 text-xs">
      <span
        className={cn(
          "inline-flex shrink-0 items-center gap-1 rounded-full border px-1.5 py-0.5 text-[10px]",
          REVIEW_TONE[review.verdict],
        )}
      >
        <Scale className="size-3" aria-hidden />
        reviewer: {review.verdict}
      </span>
      <span className="text-muted-foreground leading-relaxed">{review.reason}</span>
    </p>
  )
}

function Verdict({ verdict }: { verdict: string }) {
  const verified = verdict === "verified"
  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center gap-1 rounded-full border px-1.5 py-0.5 text-[10px]",
        verified ? "border-emerald-500/30 text-emerald-300" : "border-amber-500/30 text-amber-300",
      )}
      title={verified ? "Every quote was found verbatim in its evidence" : verdict}
    >
      {verified ? <CheckCircle2 className="size-3" aria-hidden /> : <CircleAlert className="size-3" aria-hidden />}
      {verified ? "verified" : "check quotes"}
    </span>
  )
}
