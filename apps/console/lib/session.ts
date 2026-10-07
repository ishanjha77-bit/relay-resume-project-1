import { DEMO, VISITOR } from "@/lib/demo"
import type { User } from "@/lib/types"

/**
 * The signed-in session: a JWT from the platform API, kept in sessionStorage
 * (per tab, gone when the tab closes). An external store read with
 * useSyncExternalStore: the server render sees "not read yet", the browser
 * sees the stored session, with no effect-driven re-render in between.
 */
export interface Session {
  token: string
  user: User
  expiresAt: number
}

const STORAGE_KEY = "relay.session"
let current: Session | null | undefined
const listeners = new Set<() => void>()

function read(): Session | null {
  if (DEMO) return VISITOR
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY)
    const session = raw ? (JSON.parse(raw) as Session) : null
    return session && session.expiresAt > Date.now() ? session : null
  } catch {
    return null
  }
}

export const sessionStore = {
  subscribe(listener: () => void): () => void {
    listeners.add(listener)
    return () => listeners.delete(listener)
  },
  getSnapshot(): Session | null {
    current ??= read()
    return current
  },
  /** Not known on the server: render the signed-out-or-loading state. */
  getServerSnapshot(): Session | null | undefined {
    return undefined
  },
  set(next: Session | null): void {
    current = next
    try {
      if (next) sessionStorage.setItem(STORAGE_KEY, JSON.stringify(next))
      else sessionStorage.removeItem(STORAGE_KEY)
    } catch {
      // storage unavailable (private mode): the session lives in memory only
    }
    listeners.forEach((listener) => listener())
  },
  token(): string | null {
    return typeof window === "undefined" ? null : (sessionStore.getSnapshot()?.token ?? null)
  },
}
