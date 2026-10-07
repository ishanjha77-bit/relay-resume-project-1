"use client"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { ArrowLeft, CheckCheck, FileText, Play, Square } from "lucide-react"
import Link from "next/link"
import { useParams } from "next/navigation"
import { useState } from "react"
import { toast } from "sonner"

import { AgentTrace } from "@/components/incident/agent-trace"
import { ApprovalCard } from "@/components/incident/approval-card"
import { EvidenceDialog } from "@/components/incident/evidence-dialog"
import { Hypotheses } from "@/components/incident/hypotheses"
import { Timeline } from "@/components/incident/timeline"
import { TriagePanel } from "@/components/incident/triage"
import { SeverityBadge, StatusPill } from "@/components/status"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { api } from "@/lib/api"
import { useAuth } from "@/lib/auth"
import { age, usd } from "@/lib/format"
import { useNow } from "@/lib/hooks"
import { useIncidentStream, useLive, withLiveSteps } from "@/lib/live"
import { useReplay } from "@/lib/replay"
import type { Citation, FeedbackVote } from "@/lib/types"

export function IncidentView() {
  const { id } = useParams<{ id: string }>()
  const queryClient = useQueryClient()
  const { can } = useAuth()
  const { status: liveStatus } = useLive()
  const { fresh } = useIncidentStream(id)
  const now = useNow()
  const [citation, setCitation] = useState<Citation | null>(null)

  const detail = useQuery({ queryKey: ["incident", id], queryFn: () => api.incident(id) })
  const steps = useQuery({
    queryKey: ["steps", id],
    // Steps can arrive live while a refetch is in flight; the refetch keeps them.
    queryFn: async () => withLiveSteps(await api.steps(id), queryClient.getQueryData(["steps", id])),
  })
  const resolve = useMutation({
    mutationFn: () => api.resolve(id),
    onSuccess: () => {
      toast.success("Incident resolved")
      void queryClient.invalidateQueries({ queryKey: ["incident", id] })
      void queryClient.invalidateQueries({ queryKey: ["incidents"] })
    },
    onError: (e) => toast.error(`Could not resolve: ${e.message}`),
  })
  const vote = useMutation({
    mutationFn: (body: FeedbackVote) => api.feedback(id, body),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["incident", id] }),
    onError: (e) => toast.error(`Could not record the vote: ${e.message}`),
  })
  const recorded = steps.data ?? []
  const replay = useReplay(recorded)

  if (detail.error) {
    return <p className="text-destructive p-6 text-sm">Could not load the incident: {detail.error.message}</p>
  }
  const incident = detail.data?.incident
  const trace = replay.steps
  const concluded = trace.find((s) => s.kind === "investigation.concluded")
  // While a replay plays, the verdict appears when the replay reaches it.
  const verdictShown = !replay.playing || concluded !== undefined
  const report = (concluded?.output?.report ?? null) as { impact?: string; summary?: string } | null

  return (
    <div className="flex h-full flex-col">
      <header className="border-b px-6 py-3">
        <div className="flex items-center gap-3">
          <Link
            href="/incidents"
            className="text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:ring-ring rounded p-1 focus-visible:ring-2 focus-visible:outline-none"
          >
            <ArrowLeft className="size-4" aria-hidden />
            <span className="sr-only">Back to incidents</span>
          </Link>
          {incident ? (
            <>
              <SeverityBadge severity={incident.severity} />
              <span className="text-muted-foreground font-mono text-xs">{incident.key}</span>
              <h1 className="truncate text-sm font-semibold">{incident.title}</h1>
              <StatusPill status={incident.status} />
              <span className="text-muted-foreground ml-auto hidden text-xs md:inline">
                opened {age(incident.opened_at, now)} ago ·{" "}
                {incident.cost_usd > 0 ? usd(incident.cost_usd) : "$0 (free tier)"}
              </span>
              {detail.data?.postmortem && (
                <Button size="sm" variant="outline" asChild>
                  <Link href={`/incidents/${id}/postmortem`}>
                    <FileText aria-hidden /> Postmortem
                  </Link>
                </Button>
              )}
              {incident.status !== "RESOLVED" && can("RESPONDER") && (
                <Button size="sm" variant="outline" onClick={() => resolve.mutate()} disabled={resolve.isPending}>
                  <CheckCheck aria-hidden /> Resolve
                </Button>
              )}
            </>
          ) : (
            <Skeleton className="h-5 w-96" />
          )}
        </div>
      </header>

      {(detail.data?.approvals?.length ?? 0) > 0 && (
        <ApprovalCard
          incidentId={id}
          approvals={detail.data?.approvals ?? []}
          canDecide={can("APPROVER")}
          resolved={incident?.status === "RESOLVED"}
        />
      )}

      <div className="grid min-h-0 flex-1 grid-cols-1 overflow-y-auto lg:grid-cols-[18rem_minmax(0,1fr)_22rem] lg:overflow-hidden">
        <aside
          tabIndex={0}
          className="focus-visible:ring-ring overflow-y-auto border-b p-4 focus-visible:ring-2 focus-visible:outline-none focus-visible:ring-inset lg:border-r lg:border-b-0"
          aria-label="Timeline and alerts"
        >
          {detail.data ? (
            <Timeline events={detail.data.timeline} alerts={detail.data.alerts} />
          ) : (
            <Skeleton className="h-64" />
          )}
        </aside>

        <section
          tabIndex={0}
          className="focus-visible:ring-ring overflow-y-auto p-4 focus-visible:ring-2 focus-visible:outline-none focus-visible:ring-inset"
          aria-labelledby="trace-heading"
        >
          <div className="mb-3 flex items-center gap-2">
            <h2 id="trace-heading" className="text-muted-foreground text-xs font-medium tracking-wide uppercase">
              Agent trace
            </h2>
            {detail.data?.run_id && (
              <span className="text-muted-foreground font-mono text-[10px]">{detail.data.run_id}</span>
            )}
            {recorded.length > 0 && (
              <Button
                size="sm"
                variant="ghost"
                className="ml-auto h-6 px-2 text-xs"
                onClick={replay.playing ? replay.stop : replay.start}
              >
                {replay.playing ? <Square aria-hidden /> : <Play aria-hidden />}
                {replay.playing ? "Stop replay" : "Replay"}
              </Button>
            )}
          </div>
          {steps.isPending ? (
            <div className="space-y-2">
              {Array.from({ length: 5 }, (_, i) => (
                <Skeleton key={i} className="h-8" />
              ))}
            </div>
          ) : trace.length === 0 ? (
            <p className="text-muted-foreground text-sm">Waiting for the agent to pick this incident up…</p>
          ) : (
            <AgentTrace
              steps={trace}
              fresh={replay.playing ? replay.fresh : fresh}
              live={liveStatus === "live" || replay.playing}
            />
          )}
        </section>

        <aside
          tabIndex={0}
          className="focus-visible:ring-ring overflow-y-auto border-t p-4 focus-visible:ring-2 focus-visible:outline-none focus-visible:ring-inset lg:border-t-0 lg:border-l"
          aria-labelledby="hypotheses-heading"
        >
          <h2
            id="hypotheses-heading"
            className="text-muted-foreground mb-3 text-xs font-medium tracking-wide uppercase"
          >
            Root cause
          </h2>
          {verdictShown && detail.data?.summary && (
            <p className="mb-2 text-sm leading-relaxed">{detail.data.summary}</p>
          )}
          {verdictShown && report?.impact && (
            <p className="text-muted-foreground mb-3 text-xs">
              <span className="text-foreground font-medium">Impact:</span> {report.impact}
            </p>
          )}
          {verdictShown && detail.data && detail.data.hypotheses.length > 0 ? (
            <Hypotheses
              hypotheses={detail.data.hypotheses}
              onEvidence={setCitation}
              votes={detail.data.feedback?.hypotheses}
              onVote={(h, v) =>
                vote.mutate({ hypothesis: { run_id: h.run_id, category: h.category, service: h.service }, vote: v })
              }
              canVote={can("RESPONDER")}
              evidence={
                new Map(trace.flatMap((s) => (s.kind === "tool.called" && s.evidence_id ? [[s.evidence_id, s]] : [])))
              }
            />
          ) : (
            <p className="text-muted-foreground text-sm">
              {incident?.status === "FAILED"
                ? "The investigation gave up; see the trace for why."
                : "Hypotheses appear here when the agent concludes."}
            </p>
          )}
          {detail.data?.triage && (
            <TriagePanel
              triage={detail.data.triage}
              alerting={new Set(detail.data.alerts.flatMap((a) => (a.service ? [a.service] : [])))}
              votes={detail.data.feedback?.documents}
              onVote={(document, v) => vote.mutate({ document, vote: v })}
              canVote={can("RESPONDER")}
            />
          )}
        </aside>
      </div>

      <EvidenceDialog
        citation={citation}
        step={trace.find((s) => s.kind === "tool.called" && s.evidence_id === citation?.evidence_id)}
        onOpenChange={(open) => !open && setCitation(null)}
      />
    </div>
  )
}
