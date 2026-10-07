"use client"

import { Client, type StompSubscription } from "@stomp/stompjs"
import { type QueryClient, useQueryClient } from "@tanstack/react-query"
import { createContext, type ReactNode, useContext, useEffect, useState } from "react"

import { API_URL } from "@/lib/api"
import { useAuth } from "@/lib/auth"
import { DEMO } from "@/lib/demo"
import type { AgentStep, IncidentDetail, IncidentPage, LiveMessage } from "@/lib/types"

/** One STOMP connection per tab; pages subscribe to the topics they show. */
/** "demo": the read-only demo has no platform behind it, so nothing streams. */
type Status = "connecting" | "live" | "offline" | "demo"

interface Live {
  status: Status
  client: Client | null
}

const OFFLINE: Live = { status: "offline", client: null }
const RECORDED: Live = { status: "demo", client: null }
const LiveContext = createContext<Live>(OFFLINE)

export function LiveProvider({ children }: { children: ReactNode }) {
  const { session } = useAuth()
  const queryClient = useQueryClient()
  // Set only from the socket's callbacks, never synchronously in the effect.
  const [live, setLive] = useState<Live>({ status: "connecting", client: null })

  useEffect(() => {
    if (!session || DEMO) return
    const client = new Client({
      brokerURL: `${API_URL.replace(/^http/, "ws")}/ws`,
      connectHeaders: { Authorization: `Bearer ${session.token}` },
      reconnectDelay: 3000,
      heartbeatIncoming: 10000,
      heartbeatOutgoing: 10000,
    })
    let inbox: StompSubscription | undefined
    client.onConnect = () => {
      setLive({ status: "live", client })
      inbox = client.subscribe("/topic/incidents", (frame) => {
        const message = JSON.parse(frame.body) as LiveMessage
        if (message.type === "incident") applyIncident(queryClient, message.incident)
      })
    }
    client.onWebSocketClose = () => setLive({ status: "connecting", client: null })
    client.onStompError = () => setLive({ status: "offline", client: null })
    client.activate()
    return () => {
      inbox?.unsubscribe()
      void client.deactivate()
    }
  }, [session, queryClient])

  return <LiveContext value={DEMO ? RECORDED : session ? live : OFFLINE}>{children}</LiveContext>
}

export function useLive(): Live {
  return useContext(LiveContext)
}

/**
 * Live steps and status changes of one incident, merged into its query caches.
 * Returns the steps that arrived live, so they can animate in.
 */
export function useIncidentStream(id: string): { fresh: ReadonlySet<number> } {
  const { client, status } = useLive()
  const queryClient = useQueryClient()
  const [fresh, setFresh] = useState<ReadonlySet<number>>(() => new Set())

  useEffect(() => {
    if (!client || status !== "live") return
    const subscription = client.subscribe(`/topic/incidents/${id}`, (frame) => {
      const message = JSON.parse(frame.body) as LiveMessage
      if (message.type === "step") {
        const { step } = message
        setFresh((seqs) => new Set(seqs).add(step.seq))
        queryClient.setQueryData<AgentStep[]>(["steps", id], (steps = []) => mergeStep(steps, step))
      } else {
        applyIncident(queryClient, message.incident)
        // Hypotheses, timeline and alerts changed with it.
        void queryClient.invalidateQueries({ queryKey: ["incident", id] })
      }
    })
    // Steps recorded while the socket was down.
    void queryClient.invalidateQueries({ queryKey: ["steps", id] })
    return () => subscription.unsubscribe()
  }, [client, status, id, queryClient])

  return { fresh }
}

/**
 * A fetched trace, plus steps of the same run that arrived live while the fetch was in
 * flight: the fetch must not drop them. A different run in the cache is an old one.
 */
export function withLiveSteps(fetched: AgentStep[], cached: AgentStep[] | undefined): AgentStep[] {
  if (!cached?.length || (fetched.length && cached[0].run_id !== fetched[0].run_id)) return fetched
  return cached.reduce(mergeStep, fetched)
}

function mergeStep(steps: AgentStep[], step: AgentStep): AgentStep[] {
  // A new run replaces the old trace; a redelivered step changes nothing.
  const current = steps.length && steps[0].run_id !== step.run_id ? [] : steps
  if (current.some((s) => s.seq === step.seq)) return current
  return [...current, step].sort((a, b) => a.seq - b.seq)
}

function applyIncident(queryClient: QueryClient, incident: IncidentDetail["incident"]) {
  queryClient.setQueryData<IncidentDetail>(["incident", incident.id], (detail) =>
    detail ? { ...detail, incident } : detail,
  )
  for (const [key, page] of queryClient.getQueriesData<IncidentPage>({ queryKey: ["incidents"] })) {
    if (!page) continue
    const scope = key[1] as "active" | "resolved" | "all"
    const belongs =
      scope === "all" || (scope === "resolved" ? incident.status === "RESOLVED" : incident.status !== "RESOLVED")
    const others = page.items.filter((i) => i.id !== incident.id)
    const items = belongs ? [incident, ...others].sort((a, b) => b.opened_at.localeCompare(a.opened_at)) : others
    queryClient.setQueryData<IncidentPage>(key, { ...page, items })
  }
}
