"use client"

import { useQuery } from "@tanstack/react-query"
import { Inbox } from "lucide-react"
import Link from "next/link"
import { useRouter } from "next/navigation"
import { useEffect, useMemo, useRef, useState } from "react"

import { SeverityBadge, StatusPill } from "@/components/status"
import { Kbd } from "@/components/ui/kbd"
import { Skeleton } from "@/components/ui/skeleton"
import { api } from "@/lib/api"
import { DEMO } from "@/lib/demo"
import { age, category, usd } from "@/lib/format"
import { useNow } from "@/lib/hooks"
import { isTyping } from "@/lib/keyboard"
import { cn } from "@/lib/utils"

type Scope = "active" | "resolved" | "all"

export default function IncidentsPage() {
  // The read-only demo holds resolved incidents only.
  const [scope, setScope] = useState<Scope>(DEMO ? "all" : "active")
  const [selected, setSelected] = useState(0)
  const router = useRouter()
  const now = useNow()
  const rows = useRef<(HTMLAnchorElement | null)[]>([])
  const { data, isPending, error } = useQuery({
    queryKey: ["incidents", scope],
    queryFn: () => api.incidents(scope),
    refetchInterval: 30_000,
  })
  const items = useMemo(() => data?.items ?? [], [data])

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (isTyping(event) || event.metaKey || event.ctrlKey || items.length === 0) return
      if (event.key === "j" || event.key === "ArrowDown") {
        event.preventDefault()
        setSelected((i) => Math.min(i + 1, items.length - 1))
      } else if (event.key === "k" || event.key === "ArrowUp") {
        event.preventDefault()
        setSelected((i) => Math.max(i - 1, 0))
      } else if (event.key === "Enter" && items[selected]) {
        router.push(`/incidents/${items[selected].id}`)
      }
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [items, selected, router])

  useEffect(() => {
    rows.current[selected]?.scrollIntoView({ block: "nearest" })
  }, [selected])

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center gap-4 border-b px-6 py-3">
        <h1 className="text-sm font-semibold">Incidents</h1>
        <div role="group" aria-label="Show incidents" className="bg-muted flex rounded-lg p-0.5">
          {(["active", "resolved", "all"] as const).map((value) => (
            <button
              key={value}
              type="button"
              aria-pressed={scope === value}
              onClick={() => {
                setScope(value)
                setSelected(0)
              }}
              className={cn(
                "focus-visible:ring-ring rounded-md px-2.5 py-1 text-xs capitalize transition-colors focus-visible:ring-2 focus-visible:outline-none",
                scope === value
                  ? "bg-background text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {value}
            </button>
          ))}
        </div>
        <p className="text-muted-foreground ml-auto hidden items-center gap-1.5 text-xs sm:flex">
          <Kbd>J</Kbd>
          <Kbd>K</Kbd> to move · <Kbd>Enter</Kbd> to open · <Kbd>Ctrl</Kbd>
          <Kbd>K</Kbd> to search
        </p>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto" role="list" aria-label={`${scope} incidents`}>
        {isPending &&
          Array.from({ length: 6 }, (_, i) => (
            <div key={i} className="flex items-center gap-3 border-b px-6 py-3">
              <Skeleton className="h-5 w-12" />
              <Skeleton className="h-4 w-14" />
              <Skeleton className="h-4 flex-1" />
              <Skeleton className="h-5 w-24" />
            </div>
          ))}
        {error && <p className="text-destructive px-6 py-8 text-sm">Could not load incidents: {error.message}</p>}
        {data && items.length === 0 && <EmptyInbox scope={scope} />}
        {items.map((incident, i) => (
          <Link
            key={incident.id}
            href={`/incidents/${incident.id}`}
            role="listitem"
            ref={(el) => {
              rows.current[i] = el
            }}
            onMouseEnter={() => setSelected(i)}
            onFocus={() => setSelected(i)}
            aria-current={i === selected ? "true" : undefined}
            className={cn(
              "grid grid-cols-[auto_4.5rem_minmax(0,1fr)_auto] items-center gap-3 border-b px-6 py-2.5 text-sm transition-colors outline-none md:grid-cols-[auto_4.5rem_minmax(0,1fr)_8rem_11rem_auto_3rem]",
              i === selected ? "bg-muted/70" : "hover:bg-muted/40",
              "focus-visible:ring-ring focus-visible:ring-2 focus-visible:ring-inset",
            )}
          >
            <SeverityBadge severity={incident.severity} />
            <span className="text-muted-foreground font-mono text-xs">{incident.key}</span>
            <span className="truncate">
              {incident.title}
              {incident.root_cause_category && (
                <span className="text-muted-foreground ml-2 text-xs">
                  → {category(incident.root_cause_category)} in {incident.root_cause_service}
                </span>
              )}
            </span>
            <span className="text-muted-foreground hidden truncate font-mono text-xs md:block">
              {incident.service ?? "—"}
            </span>
            <span className="hidden md:block">
              <StatusPill status={incident.status} />
            </span>
            <span className="text-muted-foreground hidden text-right font-mono text-xs md:block">
              {incident.cost_usd > 0 ? usd(incident.cost_usd, 3) : "free"}
            </span>
            <span className="text-muted-foreground text-right text-xs tabular-nums">
              <time dateTime={incident.opened_at}>{age(incident.opened_at, now)}</time>
            </span>
          </Link>
        ))}
      </div>
    </div>
  )
}

function EmptyInbox({ scope }: { scope: Scope }) {
  return (
    <div className="grid place-items-center px-6 py-24 text-center">
      <Inbox className="text-muted-foreground mb-3 size-8" aria-hidden />
      <p className="text-sm font-medium">{scope === "active" ? "No active incidents" : "Nothing here yet"}</p>
      {scope === "active" && (
        <p className="text-muted-foreground mt-1 max-w-sm text-xs">
          All quiet. Break something on purpose with{" "}
          <code className="bg-muted rounded px-1 py-0.5 font-mono">make chaos scenario=db-pool</code> and watch the
          agent take it from the alert.
        </p>
      )}
    </div>
  )
}
