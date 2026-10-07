import { BookOpen, ChevronRight, FileClock } from "lucide-react"

import type { Triage, Vote, Votes } from "@/lib/types"

import { ServiceMap } from "./service-map"
import { VoteButtons } from "./vote-buttons"

/** The triage agent's first look: where to start, what to check, and what the alerts resemble. */
export function TriagePanel({
  triage,
  alerting,
  votes = {},
  onVote,
  canVote = false,
}: {
  triage: Triage
  alerting: ReadonlySet<string>
  /** Responders' votes on the related documents, by doc_id: they re-rank the next search. */
  votes?: Record<string, Votes>
  onVote?: (docId: string, vote: Vote) => void
  canVote?: boolean
}) {
  return (
    <section aria-labelledby="triage-heading" className="mt-6">
      <h2 id="triage-heading" className="text-muted-foreground mb-2 text-xs font-medium tracking-wide uppercase">
        Triage
      </h2>
      {triage.summary ? (
        <>
          <p className="text-sm leading-relaxed">{triage.summary}</p>
          <p className="text-muted-foreground mt-1 text-xs">
            Start with <span className="text-foreground font-mono">{triage.service}</span>
            {triage.model && <> · {triage.model}</>}
          </p>
        </>
      ) : (
        <p className="text-muted-foreground text-xs">The triage model gave no assessment.</p>
      )}
      {triage.leads.length > 0 && (
        <ul className="mt-2 list-disc space-y-1 pl-4 text-xs leading-relaxed" aria-label="Leads">
          {triage.leads.map((lead, i) => (
            <li key={i}>{lead}</li>
          ))}
        </ul>
      )}
      {(triage.dependencies?.length ?? 0) > 0 && (
        <>
          <h3 className="text-muted-foreground mt-4 text-[11px] font-medium">Service map at triage</h3>
          <ServiceMap edges={triage.dependencies ?? []} alerting={alerting} />
          <dl className="text-muted-foreground mt-1.5 grid grid-cols-[auto_1fr] gap-x-2 text-[11px]">
            <dt>Who feels it</dt>
            <dd className="text-foreground font-mono">{triage.upstream?.join(", ") || "none"}</dd>
            <dt>Where it may come from</dt>
            <dd className="text-foreground font-mono">{triage.downstream?.join(", ") || "nothing"}</dd>
          </dl>
        </>
      )}
      {triage.related.length > 0 && (
        <>
          <h3 className="text-muted-foreground mt-4 mb-1.5 text-[11px] font-medium">
            Related runbooks and past incidents
          </h3>
          <ul className="space-y-1" aria-label="Related runbooks and past incidents">
            {triage.related.map((doc) => (
              <li key={doc.doc_id} className="flex items-start gap-1">
                <details className="group bg-card min-w-0 flex-1 rounded-md border text-xs">
                  <summary className="hover:bg-muted/40 focus-visible:ring-ring flex cursor-pointer list-none items-center gap-1.5 px-2 py-1.5 focus-visible:ring-2 focus-visible:outline-none">
                    <ChevronRight className="size-3 shrink-0 transition-transform group-open:rotate-90" aria-hidden />
                    {doc.kind === "postmortem" ? (
                      <FileClock className="size-3 shrink-0 text-amber-300" aria-label="Postmortem" />
                    ) : (
                      <BookOpen className="size-3 shrink-0 text-sky-300" aria-label="Runbook" />
                    )}
                    <span className="truncate">{doc.title}</span>
                    <span className="text-muted-foreground ml-auto shrink-0">{doc.section}</span>
                  </summary>
                  <p className="text-muted-foreground border-t px-2 py-1.5 leading-relaxed whitespace-pre-wrap">
                    {doc.text}
                  </p>
                </details>
                {onVote && (
                  <VoteButtons
                    votes={votes[doc.doc_id]}
                    onVote={(vote) => onVote(doc.doc_id, vote)}
                    disabled={!canVote}
                    up="helpful"
                    down="not helpful"
                    label={`Was ${doc.title} helpful?`}
                  />
                )}
              </li>
            ))}
          </ul>
          <p className="text-muted-foreground mt-1.5 text-[11px]">
            Background for the investigation, not evidence about this incident.
          </p>
        </>
      )}
    </section>
  )
}
