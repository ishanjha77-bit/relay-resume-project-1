import { demoIncidentIds } from "@/lib/demo-static"

import { PostmortemView } from "./postmortem-view"

/** The static demo has a page per recorded incident; elsewhere pages render on demand. */
export function generateStaticParams() {
  return demoIncidentIds()
}

export default function PostmortemPage() {
  return <PostmortemView />
}
