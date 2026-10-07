import { demoIncidentIds } from "@/lib/demo-static"

import { IncidentView } from "./incident-view"

/** The static demo has a page per recorded incident; elsewhere pages render on demand. */
export function generateStaticParams() {
  return demoIncidentIds()
}

export default function IncidentPage() {
  return <IncidentView />
}
