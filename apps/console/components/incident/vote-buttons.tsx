import { ThumbsDown, ThumbsUp } from "lucide-react"

import type { Vote, Votes } from "@/lib/types"
import { cn } from "@/lib/utils"

/**
 * Up and down votes with their counts. Pressing your own vote again takes it back.
 * `up` and `down` name what a vote means here, e.g. "helpful" / "not helpful".
 */
export function VoteButtons({
  votes,
  onVote,
  disabled,
  up,
  down,
  label,
}: {
  votes?: Votes
  onVote: (vote: Vote) => void
  disabled?: boolean
  up: string
  down: string
  label: string
}) {
  const mine = votes?.mine ?? 0
  const button =
    "focus-visible:ring-ring inline-flex items-center gap-1 rounded px-1 py-0.5 text-[10px] tabular-nums " +
    "focus-visible:ring-2 focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-60 " +
    "enabled:hover:bg-muted"
  return (
    <span role="group" aria-label={label} className="text-muted-foreground inline-flex shrink-0 items-center">
      <button
        type="button"
        aria-pressed={mine === 1}
        disabled={disabled}
        onClick={() => onVote(mine === 1 ? "none" : "up")}
        title={disabled ? `Responders can mark this ${up}` : `Mark ${up}`}
        className={cn(button, mine === 1 && "text-emerald-300")}
      >
        <ThumbsUp className="size-3" aria-hidden />
        {votes?.up ?? 0}
        <span className="sr-only">{up}</span>
      </button>
      <button
        type="button"
        aria-pressed={mine === -1}
        disabled={disabled}
        onClick={() => onVote(mine === -1 ? "none" : "down")}
        title={disabled ? `Responders can mark this ${down}` : `Mark ${down}`}
        className={cn(button, mine === -1 && "text-rose-300")}
      >
        <ThumbsDown className="size-3" aria-hidden />
        {votes?.down ?? 0}
        <span className="sr-only">{down}</span>
      </button>
    </span>
  )
}
