"use client"

import { createContext, type ReactNode, useCallback, useContext, useMemo, useSyncExternalStore } from "react"

import { api } from "@/lib/api"
import { type Session, sessionStore } from "@/lib/session"

interface Auth {
  /** undefined until the browser has read the stored session */
  session: Session | null | undefined
  login: (username: string, password: string) => Promise<void>
  logout: () => void
  can: (role: "VIEWER" | "RESPONDER" | "APPROVER") => boolean
}

const AuthContext = createContext<Auth | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const session = useSyncExternalStore(sessionStore.subscribe, sessionStore.getSnapshot, sessionStore.getServerSnapshot)

  const login = useCallback(async (username: string, password: string) => {
    const response = await api.login(username, password)
    sessionStore.set({
      token: response.access_token,
      user: { username: response.username, display_name: response.display_name, roles: response.roles },
      expiresAt: Date.now() + response.expires_in * 1000,
    })
  }, [])

  const logout = useCallback(() => sessionStore.set(null), [])

  const value = useMemo<Auth>(
    () => ({
      session,
      login,
      logout,
      can: (role) => Boolean(session?.user.roles.includes(role)),
    }),
    [session, login, logout],
  )
  return <AuthContext value={value}>{children}</AuthContext>
}

export function useAuth(): Auth {
  const auth = useContext(AuthContext)
  if (!auth) throw new Error("useAuth outside AuthProvider")
  return auth
}
