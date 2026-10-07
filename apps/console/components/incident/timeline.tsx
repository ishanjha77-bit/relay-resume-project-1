import {
  Bell,
  BellOff,
  Bot,
  CheckCircle2,
  CircleDot,
  GitPullRequestDraft,
  Hand,
  Radar,
  Scale,
  ShieldAlert,
  ShieldCheck,
  ShieldX,
  Stethoscope,
  XCircle,
} from "lucide-react"

import { clock } from "@/lib/format"
import type { IncidentAlert, TimelineEvent } from "@/lib/types"
import { cn } from "@/lib/utils"

const ICONS: Record<string, { icon: typeof Bell; className: string }> = {
  "incident.opened": { icon: CircleDot, className: "text-sky-300" },
  "alert.firing": { icon: Bell, className: "text-rose-300" },
  "alert.resolved": { icon: BellOff, className: "text-emerald-300" },
  "agent.triaged": { icon: Radar, className: "text-violet-300" },
  "agent.started": { icon: Bot, className: "text-violet-300" },
  "agent.diagnosed": { icon: Stethoscope, className: "text-amber-300" },
  "agent.failed": { icon: XCircle, className: "text-rose-300" },
  "security.injection": { icon: ShieldAlert, className: "text-rose-300" },
  "incident.resolved": { icon: CheckCircle2, className: "text-emerald-300" },
  "agent.reviewed": { icon: Scale, className: "text-sky-300" },
  "approval.requested": { icon: Hand, className: "text-amber-300" },
  "approval.approved": { icon: ShieldCheck, className: "text-emerald-300" },
  "approval.rejected": { icon: ShieldX, className: "text-rose-300" },
  "action.executed": { icon: GitPullRequestDraft, className: "text-emerald-300" },
  "action.failed": { icon: XCircle, className: "text-rose-300" },
}

export function Timeline({ events, alerts }: { events: TimelineEvent[]; alerts: IncidentAlert[] }) {
  return (
    <div className="space-y-6">
      <section aria-labelledby="alerts-heading">
        <h2 id="alerts-heading" className="text-muted-foreground mb-2 text-xs font-medium tracking-wide uppercase">
          Alerts
        </h2>
        <ul className="space-y-1.5">
          {alerts.map((alert) => (
            <li key={alert.fingerprint} className="bg-card rounded-md border px-2.5 py-1.5 text-xs">
              <div className="flex items-center gap-1.5">
                <span
                  className={cn("size-1.5 rounded-full", alert.status === "firing" ? "bg-rose-400" : "bg-emerald-400")}
                  aria-hidden
                />
                <span className="font-medium">{alert.name}</span>
                <span className="text-muted-foreground font-mono">{alert.service}</span>
                <span className="text-muted-foreground ml-auto">{alert.status}</span>
              </div>
              {alert.summary && <p className="text-muted-foreground mt-0.5">{alert.summary}</p>}
            </li>
          ))}
        </ul>
      </section>
      <section aria-labelledby="timeline-heading">
        <h2 id="timeline-heading" className="text-muted-foreground mb-2 text-xs font-medium tracking-wide uppercase">
          Timeline
        </h2>
        <ol className="relative space-y-3 border-l pl-4">
          {events.map((event) => {
            const { icon: Icon, className } = ICONS[event.kind] ?? {
              icon: CircleDot,
              className: "text-muted-foreground",
            }
            return (
              <li key={event.id} className="relative text-xs">
                <span className="bg-background absolute top-0.5 -left-[1.4rem] grid size-4 place-items-center rounded-full border">
                  <Icon className={cn("size-2.5", className)} aria-hidden />
                </span>
                <time dateTime={event.at} className="text-muted-foreground font-mono text-[10px] tabular-nums">
                  {clock(event.at)}
                </time>
                <p className="leading-snug break-words">{event.message}</p>
              </li>
            )
          })}
        </ol>
      </section>
    </div>
  )
}
