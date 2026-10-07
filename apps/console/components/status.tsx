import { AlertOctagon, CheckCircle2, CircleDashed, Hand, Loader2, Search, XCircle } from "lucide-react"

import { cn } from "@/lib/utils"
import type { IncidentStatus, Severity } from "@/lib/types"

const STATUS: Record<IncidentStatus, { label: string; icon: typeof Search; className: string }> = {
  OPEN: { label: "Open", icon: CircleDashed, className: "text-sky-300 bg-sky-500/10 border-sky-500/30" },
  INVESTIGATING: {
    label: "Investigating",
    icon: Loader2,
    className: "text-violet-300 bg-violet-500/10 border-violet-500/30",
  },
  DIAGNOSED: { label: "Diagnosed", icon: Search, className: "text-amber-300 bg-amber-500/10 border-amber-500/30" },
  AWAITING_APPROVAL: {
    label: "Awaiting approval",
    icon: Hand,
    className: "text-orange-300 bg-orange-500/10 border-orange-500/30",
  },
  RESOLVED: {
    label: "Resolved",
    icon: CheckCircle2,
    className: "text-emerald-300 bg-emerald-500/10 border-emerald-500/30",
  },
  FAILED: { label: "Needs a human", icon: XCircle, className: "text-rose-300 bg-rose-500/10 border-rose-500/30" },
}

/** Text and icon, not colour alone. */
export function StatusPill({ status, className }: { status: IncidentStatus; className?: string }) {
  const { label, icon: Icon, className: tone } = STATUS[status] ?? STATUS.OPEN
  return (
    <span
      className={cn(
        "inline-flex h-5 items-center gap-1 rounded-full border px-2 text-[11px] font-medium whitespace-nowrap",
        tone,
        className,
      )}
    >
      <Icon className={cn("size-3", status === "INVESTIGATING" && "animate-spin")} aria-hidden />
      {label}
    </span>
  )
}

const SEVERITY: Record<Severity, { label: string; className: string }> = {
  critical: { label: "SEV1", className: "bg-rose-500/15 text-rose-300 border-rose-500/30" },
  warning: { label: "SEV2", className: "bg-amber-500/15 text-amber-300 border-amber-500/30" },
  info: { label: "SEV3", className: "bg-sky-500/15 text-sky-300 border-sky-500/30" },
}

export function SeverityBadge({ severity }: { severity: Severity }) {
  const { label, className } = SEVERITY[severity] ?? SEVERITY.warning
  return (
    <span
      title={severity}
      className={cn(
        "inline-flex h-5 items-center gap-1 rounded border px-1.5 font-mono text-[10px] font-semibold",
        className,
      )}
    >
      {severity === "critical" && <AlertOctagon className="size-3" aria-hidden />}
      {label}
    </span>
  )
}
