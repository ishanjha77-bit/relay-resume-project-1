"use client"

import { ShieldAlert } from "lucide-react"

import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import type { AgentStep, Citation } from "@/lib/types"

/** The exact tool output a citation points at, with the quoted text marked. */
export function EvidenceDialog({
  citation,
  step,
  onOpenChange,
}: {
  citation: Citation | null
  step: AgentStep | undefined
  onOpenChange: (open: boolean) => void
}) {
  const output = (step?.output?.output as string | undefined) ?? ""
  const at = citation ? output.indexOf(citation.quote) : -1
  const injection = (step?.output?.injection_markers as string[] | undefined) ?? []

  return (
    <Dialog open={citation !== null} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-hidden sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 font-mono text-sm">
            <span className="bg-muted rounded px-1.5 py-0.5">{citation?.evidence_id}</span>
            {step?.tool ?? "unknown tool"}
          </DialogTitle>
          <DialogDescription>{citation?.shows}</DialogDescription>
        </DialogHeader>
        {step?.input && (
          <pre className="bg-muted/50 text-muted-foreground rounded-md px-3 py-2 font-mono text-xs">
            {JSON.stringify(step.input)}
          </pre>
        )}
        {injection.length > 0 && (
          <p className="flex items-center gap-2 rounded-md border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-xs text-rose-300">
            <ShieldAlert className="size-4" aria-hidden />
            Untrusted text in this output looked like instructions to an AI ({injection.join(", ")}). The agent treats
            it as data.
          </p>
        )}
        {!step ? (
          <p className="text-muted-foreground text-sm">This evidence is not in the current trace.</p>
        ) : (
          <>
            {at < 0 && (
              <p className="text-xs text-amber-300">
                The quote does not appear verbatim in this output, so the citation is marked unverified.
              </p>
            )}
            <pre className="bg-background max-h-[50vh] overflow-auto rounded-md border p-3 font-mono text-xs leading-relaxed break-words whitespace-pre-wrap">
              {at < 0 || !citation ? (
                output
              ) : (
                <>
                  {output.slice(0, at)}
                  <mark className="text-foreground rounded-sm bg-amber-400/30 px-0.5 ring-1 ring-amber-400/60">
                    {output.slice(at, at + citation.quote.length)}
                  </mark>
                  {output.slice(at + citation.quote.length)}
                </>
              )}
            </pre>
          </>
        )}
      </DialogContent>
    </Dialog>
  )
}
