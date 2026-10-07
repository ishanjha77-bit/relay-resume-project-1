import type { Session } from "@/lib/session"

/**
 * The public read-only demo: a static build of the console that serves incidents
 * Relay really investigated, exported from the platform API (`make demo-site`).
 * Visitors are viewers: nothing can be approved, resolved or changed.
 */
export const DEMO = process.env.NEXT_PUBLIC_DEMO === "1"

/** Where the static site is served from, e.g. "/relay" on GitHub Pages. */
export const BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH ?? ""

/** The repository the demo links to for running Relay yourself, if set. */
export const REPO_URL = process.env.NEXT_PUBLIC_REPO_URL ?? ""

export const VISITOR: Session = {
  token: "demo",
  user: { username: "visitor", display_name: "Visitor (read-only demo)", roles: ["VIEWER"] },
  expiresAt: Number.MAX_SAFE_INTEGER,
}

/** The recorded file that answers an API GET, or null if the demo doesn't have it. */
export function demoFile(path: string): string | null {
  const url = new URL(path, "http://demo")
  const p = url.pathname
  if (p === "/api/incidents") return "incidents.json"
  if (p === "/api/evals/batches") return "evals/batches.json"
  if (p === "/api/evals/runs") return "evals/runs.json"
  const steps = p.match(/^\/api\/incidents\/([0-9a-f-]{36})\/steps$/)
  if (steps) return `steps/${steps[1]}.json`
  const incident = p.match(/^\/api\/incidents\/([0-9a-f-]{36})$/)
  if (incident) return `incidents/${incident[1]}.json`
  return null
}

/** The recorded answer, narrowed the way the API would narrow it. */
export function demoFilter(path: string, body: unknown): unknown {
  const url = new URL(path, "http://demo")
  if (url.pathname === "/api/incidents") {
    const status = url.searchParams.get("status") ?? "active"
    const page = body as { items: { status: string }[]; next_cursor: string | null }
    const keep = (s: string) => status === "all" || (status === "resolved" ? s === "RESOLVED" : s !== "RESOLVED")
    return { ...page, items: page.items.filter((i) => keep(i.status)) }
  }
  if (url.pathname === "/api/evals/runs" && url.searchParams.get("batch")) {
    return (body as { batch: string }[]).filter((r) => r.batch === url.searchParams.get("batch"))
  }
  return body
}
