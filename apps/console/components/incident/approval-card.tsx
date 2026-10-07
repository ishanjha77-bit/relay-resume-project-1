"use client"

import { useMutation, useQueryClient } from "@tanstack/react-query"
import { motion } from "motion/react"
import { ChevronRight, ExternalLink, GitPullRequestDraft, Hand, ShieldCheck, ShieldX, XCircle } from "lucide-react"
import { useId, useState } from "react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { api } from "@/lib/api"
import { DEMO } from "@/lib/demo"
import { clock } from "@/lib/format"
import type { Approval } from "@/lib/types"
import { cn } from "@/lib/utils"

const RISK: Record<Approval["risk"], string> = {
  low: "border-emerald-500/40 bg-emerald-500/10 text-emerald-300",
  medium: "border-amber-500/40 bg-amber-500/10 text-amber-300",
  high: "border-rose-500/40 bg-rose-500/10 text-rose-300",
}

/**
 * What an agent wants to do, for a human to approve or reject: the change as a
 * diff, why, how risky, and the exact action that will run. Only approvers can
 * decide; everyone else sees the request and who decided it.
 */
export function ApprovalCard({
  incidentId,
  approvals,
  canDecide,
  resolved = false,
}: {
  incidentId: string
  approvals: Approval[]
  canDecide: boolean
  /** The incident is resolved: nothing still pending can be decided any more. */
  resolved?: boolean
}) {
  // The pending request if there is one, else the latest outcome.
  const approval = approvals.find((a) => a.status === "PENDING") ?? approvals[0]
  if (!approval) return null
  if (resolved && approval.status === "PENDING") {
    // Recorded before resolving closed pending requests (platform-api does so now).
    return <Outcome approval={approval} closed />
  }
  return approval.status === "PENDING" ? (
    <PendingApproval key={approval.id} incidentId={incidentId} approval={approval} canDecide={canDecide} />
  ) : (
    <Outcome approval={approval} />
  )
}

function PendingApproval({
  incidentId,
  approval,
  canDecide,
}: {
  incidentId: string
  approval: Approval
  canDecide: boolean
}) {
  const queryClient = useQueryClient()
  const [dialog, setDialog] = useState<"approve" | "reject" | null>(null)
  const [reason, setReason] = useState("")
  const [showAction, setShowAction] = useState(false)
  const reasonId = useId()
  const decide = useMutation({
    mutationFn: (decision: "approve" | "reject") => api.decide(incidentId, approval.id, decision, reason.trim()),
    // The toast follows what was decided: by the time the response lands, an approved
    // action may already be EXECUTED or FAILED.
    onSuccess: (_decided, decision) => {
      toast.success(decision === "approve" ? "Approved: the agent will open the pull request" : "Rejected")
      setDialog(null)
      void queryClient.invalidateQueries({ queryKey: ["incident", incidentId] })
      void queryClient.invalidateQueries({ queryKey: ["incidents"] })
    },
    onError: (e) => toast.error(`Could not record the decision: ${e.message}`),
  })
  const rejecting = dialog === "reject"

  return (
    <motion.section
      initial={{ opacity: 0, y: -8 }}
      animate={{ opacity: 1, y: 0 }}
      aria-labelledby="approval-heading"
      className="border-b border-amber-500/30 bg-amber-500/5 px-6 py-3"
    >
      <div className="flex flex-wrap items-start gap-3">
        <Hand className="mt-0.5 size-4 shrink-0 text-amber-300" aria-hidden />
        <div className="min-w-0 flex-1">
          <h2 id="approval-heading" className="text-sm font-semibold">
            <span className="text-amber-300">Relay asks to: </span>
            {approval.title}
          </h2>
          <p className="text-muted-foreground mt-0.5 text-xs leading-relaxed">{approval.rationale}</p>
        </div>
        <span className={cn("rounded border px-1.5 py-0.5 text-[11px] font-medium", RISK[approval.risk])}>
          risk: {approval.risk}
        </span>
        <div className="flex items-center gap-2">
          <Button size="sm" onClick={() => setDialog("approve")} disabled={!canDecide}>
            <ShieldCheck aria-hidden /> Approve
          </Button>
          <Button size="sm" variant="outline" onClick={() => setDialog("reject")} disabled={!canDecide}>
            <ShieldX aria-hidden /> Reject
          </Button>
        </div>
      </div>
      {!canDecide && (
        <p className="text-muted-foreground mt-1 pl-7 text-xs">Only someone with the approver role can decide.</p>
      )}

      {approval.diff && <Diff diff={approval.diff} />}

      <Collapsible open={showAction} onOpenChange={setShowAction} className="mt-2 pl-7">
        <CollapsibleTrigger className="text-muted-foreground hover:text-foreground focus-visible:ring-ring flex items-center gap-1 rounded text-xs focus-visible:ring-2 focus-visible:outline-none">
          <ChevronRight className={cn("size-3 transition-transform", showAction && "rotate-90")} aria-hidden />
          The exact action, as approved (sha256 {approval.action_sha256.slice(0, 12)}…)
        </CollapsibleTrigger>
        <CollapsibleContent>
          <pre className="bg-muted/50 mt-1 max-h-48 overflow-auto rounded-md px-3 py-2 font-mono text-[11px] whitespace-pre-wrap">
            {pretty(approval.action)}
          </pre>
        </CollapsibleContent>
      </Collapsible>

      <Dialog open={dialog !== null} onOpenChange={(open) => !open && setDialog(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{rejecting ? "Reject this change?" : "Approve this change?"}</DialogTitle>
            <DialogDescription>
              {rejecting
                ? "Nothing will be changed. The reason goes on the incident's timeline."
                : "The agent will open a draft pull request with exactly this change. Nothing is merged or deployed until someone reviews it."}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-1.5">
            <Label htmlFor={reasonId}>{rejecting ? "Reason (required)" : "Note (optional)"}</Label>
            <Textarea
              id={reasonId}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder={rejecting ? "Why not?" : "e.g. matches the deploy timeline"}
              maxLength={2000}
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDialog(null)}>
              Cancel
            </Button>
            <Button
              variant={rejecting ? "destructive" : "default"}
              disabled={decide.isPending || (rejecting && !reason.trim())}
              onClick={() => dialog && decide.mutate(dialog)}
            >
              {rejecting ? "Reject" : "Approve"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </motion.section>
  )
}

function Outcome({ approval, closed = false }: { approval: Approval; closed?: boolean }) {
  const by = approval.decided_by ? `${approval.decided_by} at ${clock(approval.decided_at ?? "")}` : ""
  let body: React.ReactNode
  let tone = "text-muted-foreground"
  let Icon = ShieldCheck
  switch (closed ? "CLOSED" : approval.status) {
    case "CLOSED":
      Icon = ShieldX
      body = <>Not decided: the incident was resolved first, so nothing ran.</>
      break
    case "APPROVED":
      body = <>Approved by {by}; the agent is opening the pull request…</>
      break
    case "EXECUTED":
      Icon = GitPullRequestDraft
      tone = "text-emerald-300"
      body = (
        <>
          Approved by {by}.{" "}
          {approval.result?.url && DEMO ? (
            // The demo's pull requests live on the recording machine's Gitea, not on the internet.
            <span className="text-foreground">
              Draft pull request #{approval.result.pull_request} opened on the deploy repo
            </span>
          ) : approval.result?.url ? (
            <a
              href={approval.result.url}
              target="_blank"
              rel="noreferrer"
              className="text-foreground focus-visible:ring-ring inline-flex items-center gap-1 rounded underline underline-offset-2 focus-visible:ring-2 focus-visible:outline-none"
            >
              Draft pull request #{approval.result.pull_request} <ExternalLink className="size-3" aria-hidden />
            </a>
          ) : (
            "Done."
          )}
        </>
      )
      break
    case "REJECTED":
      Icon = ShieldX
      body = (
        <>
          Rejected by {by}
          {approval.decision_reason ? `: ${approval.decision_reason}` : ""}
        </>
      )
      break
    default:
      Icon = XCircle
      tone = "text-rose-300"
      body = (
        <>
          Approved by {by}, but it failed: {approval.error}
        </>
      )
  }
  return (
    <section aria-label="Agent action" className="border-b px-6 py-2 text-xs">
      <p className="flex flex-wrap items-center gap-2">
        <Icon className={cn("size-3.5", tone)} aria-hidden />
        <span className="font-medium">{approval.title}</span>
        <span className="text-muted-foreground">{body}</span>
      </p>
    </section>
  )
}

function Diff({ diff }: { diff: string }) {
  const lines = diff.split("\n").filter((line, i, all) => line || i < all.length - 1)
  return (
    <pre
      tabIndex={0}
      aria-label="The change, as a diff"
      className="bg-card focus-visible:ring-ring mt-2 ml-7 max-h-56 overflow-auto rounded-md border py-1.5 font-mono text-[11px] leading-5 focus-visible:ring-2 focus-visible:outline-none"
    >
      {lines.map((line, i) => (
        <span
          key={i}
          className={cn(
            "block px-3",
            line.startsWith("+") && !line.startsWith("+++") && "bg-emerald-500/10 text-emerald-300",
            line.startsWith("-") && !line.startsWith("---") && "bg-rose-500/10 text-rose-300",
            line.startsWith("@@") && "text-sky-300",
            (line.startsWith("diff") || line.startsWith("index") || line.startsWith("+++") || line.startsWith("---")) &&
              "text-muted-foreground",
          )}
        >
          {line || " "}
        </span>
      ))}
    </pre>
  )
}

function pretty(action: string): string {
  try {
    return JSON.stringify(JSON.parse(action), null, 2)
  } catch {
    return action
  }
}
