import { readFileSync } from "node:fs"
import path from "node:path"

/** The recorded incidents' ids, one static page each (`make demo-site`); none outside the demo. */
export function demoIncidentIds(): { id: string }[] {
  if (process.env.NEXT_PUBLIC_DEMO !== "1") return []
  const file = path.join(process.cwd(), "public", "demo", "incidents.json")
  const { items } = JSON.parse(readFileSync(file, "utf-8")) as { items: { id: string }[] }
  return items.map(({ id }) => ({ id }))
}
