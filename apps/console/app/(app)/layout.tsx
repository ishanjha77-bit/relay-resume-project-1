"use client"

import { BarChart3, Inbox, LogOut, Radio, Settings } from "lucide-react"
import Link from "next/link"
import { usePathname, useRouter } from "next/navigation"
import { type ReactNode, useEffect } from "react"

import { CommandPalette } from "@/components/command-palette"
import { Kbd } from "@/components/ui/kbd"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { useAuth } from "@/lib/auth"
import { DEMO, REPO_URL } from "@/lib/demo"
import { isTyping } from "@/lib/keyboard"
import { useLive } from "@/lib/live"
import { cn } from "@/lib/utils"

const NAV = [
  { href: "/incidents", label: "Incidents", icon: Inbox, key: "i" },
  { href: "/evals", label: "Evals", icon: BarChart3, key: "e" },
  { href: "/settings", label: "Settings", icon: Settings, key: "s" },
]

export default function AppLayout({ children }: { children: ReactNode }) {
  const { session, logout } = useAuth()
  const { status } = useLive()
  const router = useRouter()
  const pathname = usePathname()

  useEffect(() => {
    if (session === null) router.replace("/login")
  }, [session, router])

  // "g" then a letter: go to a page (like Linear / GitHub).
  useEffect(() => {
    let pending = false
    let timer: ReturnType<typeof setTimeout> | undefined
    const onKey = (event: KeyboardEvent) => {
      if (isTyping(event)) return
      if (pending) {
        const target = NAV.find((n) => n.key === event.key.toLowerCase())
        pending = false
        if (target) router.push(target.href)
      } else if (event.key === "g" && !event.metaKey && !event.ctrlKey) {
        pending = true
        clearTimeout(timer)
        timer = setTimeout(() => (pending = false), 1000)
      }
    }
    window.addEventListener("keydown", onKey)
    return () => {
      window.removeEventListener("keydown", onKey)
      clearTimeout(timer)
    }
  }, [router])

  if (!session) return null

  return (
    <div className="flex h-screen overflow-hidden">
      <nav aria-label="Main" className="bg-sidebar flex w-14 shrink-0 flex-col items-center gap-1 border-r py-3">
        <Link
          href="/incidents"
          className="bg-primary text-primary-foreground mb-3 grid size-8 place-items-center rounded-md"
        >
          <Radio className="size-4" aria-hidden />
          <span className="sr-only">Relay</span>
        </Link>
        {NAV.map(({ href, label, icon: Icon, key }) => {
          const active = pathname.startsWith(href)
          return (
            <Tooltip key={href}>
              <TooltipTrigger asChild>
                <Link
                  href={href}
                  aria-current={active ? "page" : undefined}
                  className={cn(
                    "text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:ring-ring grid size-9 place-items-center rounded-md transition-colors focus-visible:ring-2 focus-visible:outline-none",
                    active && "bg-muted text-foreground",
                  )}
                >
                  <Icon className="size-4" aria-hidden />
                  <span className="sr-only">{label}</span>
                </Link>
              </TooltipTrigger>
              <TooltipContent side="right">
                {label} <Kbd>G</Kbd> <Kbd>{key.toUpperCase()}</Kbd>
              </TooltipContent>
            </Tooltip>
          )
        })}
        <div className="mt-auto flex flex-col items-center gap-3">
          <Tooltip>
            <TooltipTrigger asChild>
              <span
                role="status"
                className={cn(
                  "size-2.5 rounded-full",
                  status === "live" ? "bg-emerald-400 shadow-[0_0_8px] shadow-emerald-400/60" : "bg-amber-400",
                )}
              >
                <span className="sr-only">Live updates {status}</span>
              </span>
            </TooltipTrigger>
            <TooltipContent side="right">
              {status === "demo" ? "Recorded incidents: a read-only demo" : `Live updates: ${status}`}
            </TooltipContent>
          </Tooltip>
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                type="button"
                onClick={logout}
                className="bg-muted hover:bg-muted/70 focus-visible:ring-ring grid size-9 place-items-center rounded-full text-xs font-semibold uppercase focus-visible:ring-2 focus-visible:outline-none"
              >
                {session.user.username.slice(0, 2)}
                <span className="sr-only">Sign out</span>
              </button>
            </TooltipTrigger>
            <TooltipContent side="right">
              <LogOut className="mr-1 inline size-3" aria-hidden />
              Sign out {session.user.display_name}
            </TooltipContent>
          </Tooltip>
        </div>
      </nav>
      <main id="main" className="flex min-w-0 flex-1 flex-col overflow-hidden">
        {DEMO && (
          <p className="border-b bg-amber-500/10 px-4 py-1.5 text-center text-xs text-amber-200">
            Read-only demo: real incidents from Relay&apos;s eval runs, as the platform recorded them.{" "}
            {REPO_URL ? (
              <a href={`${REPO_URL}#quick-start`} className="underline underline-offset-2 hover:text-amber-100">
                Run it yourself
              </a>
            ) : (
              "Run it yourself"
            )}{" "}
            to watch investigations live.
          </p>
        )}
        <div className="min-h-0 flex-1">{children}</div>
      </main>
      <CommandPalette />
    </div>
  )
}
