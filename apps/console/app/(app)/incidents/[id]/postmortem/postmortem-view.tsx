"use client"

import { useQuery } from "@tanstack/react-query"
import { ArrowLeft, Copy, Download } from "lucide-react"
import Link from "next/link"
import { useParams } from "next/navigation"
import type { ReactNode } from "react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { api } from "@/lib/api"
import { clock } from "@/lib/format"
import type { PostmortemDocument } from "@/lib/types"
import { cn } from "@/lib/utils"

const PRIORITY = {
  high: "border-rose-500/40 bg-rose-500/10 text-rose-300",
  medium: "border-amber-500/40 bg-amber-500/10 text-amber-300",
  low: "border-sky-500/40 bg-sky-500/10 text-sky-300",
} as const

/** The incident's blameless postmortem, as a document; exportable as Markdown. */
export function PostmortemView() {
  const { id } = useParams<{ id: string }>()
  const detail = useQuery({ queryKey: ["incident", id], queryFn: () => api.incident(id) })
  const incident = detail.data?.incident
  const postmortem = detail.data?.postmortem

  function download() {
    if (!postmortem || !incident) return
    const url = URL.createObjectURL(new Blob([postmortem.markdown], { type: "text/markdown" }))
    const link = Object.assign(document.createElement("a"), { href: url, download: `${incident.key}-postmortem.md` })
    link.click()
    URL.revokeObjectURL(url)
  }

  async function copy() {
    if (!postmortem) return
    await navigator.clipboard.writeText(postmortem.markdown)
    toast.success("Markdown copied")
  }

  return (
    <div className="h-full overflow-y-auto">
      <header className="bg-background/80 sticky top-0 z-10 border-b px-6 py-3 backdrop-blur">
        <div className="mx-auto flex max-w-3xl items-center gap-3">
          <Link
            href={`/incidents/${id}`}
            className="text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:ring-ring rounded p-1 focus-visible:ring-2 focus-visible:outline-none"
          >
            <ArrowLeft className="size-4" aria-hidden />
            <span className="sr-only">Back to the incident</span>
          </Link>
          <span className="text-muted-foreground font-mono text-xs">{incident?.key}</span>
          <span className="text-sm font-medium">Postmortem</span>
          <div className="ml-auto flex gap-2">
            <Button size="sm" variant="outline" onClick={copy} disabled={!postmortem}>
              <Copy aria-hidden /> Copy Markdown
            </Button>
            <Button size="sm" onClick={download} disabled={!postmortem}>
              <Download aria-hidden /> Export Markdown
            </Button>
          </div>
        </div>
      </header>

      <article className="mx-auto max-w-3xl px-6 py-8">
        {detail.isPending ? (
          <div className="space-y-3">
            <Skeleton className="h-8 w-2/3" />
            <Skeleton className="h-24" />
            <Skeleton className="h-24" />
          </div>
        ) : !postmortem ? (
          <p className="text-muted-foreground text-sm">
            No postmortem yet. Relay drafts one when someone resolves an investigated incident.
          </p>
        ) : (
          <Document
            doc={postmortem.document}
            title={`${incident?.key}: ${postmortem.document.title}`}
            meta={`Blameless postmortem · resolved ${clock(incident?.resolved_at ?? postmortem.written_at)} · drafted by Relay (${postmortem.model}) from the incident's record`}
          />
        )}
      </article>
    </div>
  )
}

function Document({ doc, title, meta }: { doc: PostmortemDocument; title: string; meta: string }) {
  return (
    <>
      <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
      <p className="text-muted-foreground mt-1 text-xs">{meta}</p>
      <Section title="Summary">{doc.summary}</Section>
      <Section title="Impact">{doc.impact}</Section>
      <Section title="Detection">{doc.detection}</Section>
      <Section title="Root cause">{doc.root_cause}</Section>
      <Section title="Resolution">{doc.resolution}</Section>
      <Section title="Timeline (UTC)">
        <ol className="border-l pl-4">
          {doc.timeline.map((t, i) => (
            <li key={i} className="relative pb-2 text-sm">
              <span className="bg-muted-foreground absolute top-1.5 -left-[1.3rem] size-1.5 rounded-full" aria-hidden />
              <time dateTime={t.at} className="text-muted-foreground font-mono text-xs">
                {clock(t.at)}
              </time>{" "}
              {t.event}
            </li>
          ))}
        </ol>
      </Section>
      <Section title="Action items">
        <ul className="space-y-1.5">
          {doc.action_items.map((a, i) => (
            <li key={i} className="flex items-start gap-2 text-sm">
              <span
                className={cn("mt-0.5 rounded border px-1.5 text-[10px] font-medium uppercase", PRIORITY[a.priority])}
              >
                {a.priority}
              </span>
              <span>
                {a.item}
                {a.owner && <span className="text-muted-foreground"> ({a.owner})</span>}
              </span>
            </li>
          ))}
        </ul>
      </Section>
      <Section title="Lessons">
        <ul className="list-disc space-y-1 pl-5 text-sm">
          {doc.lessons.map((l, i) => (
            <li key={i}>{l}</li>
          ))}
        </ul>
      </Section>
    </>
  )
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="mt-6">
      <h2 className="text-muted-foreground mb-2 text-xs font-medium tracking-wide uppercase">{title}</h2>
      {typeof children === "string" ? <p className="text-sm leading-relaxed">{children}</p> : children}
    </section>
  )
}
