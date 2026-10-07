"use client"

import { Radio } from "lucide-react"
import { useRouter } from "next/navigation"
import { type FormEvent, useEffect, useState } from "react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { ApiError } from "@/lib/api"
import { useAuth } from "@/lib/auth"
import { DEMO } from "@/lib/demo"

const DEMO_USERS = [
  { username: "alice", roles: "approver · responder · viewer" },
  { username: "bob", roles: "responder · viewer" },
  { username: "vic", roles: "viewer" },
]

export default function LoginPage() {
  const { session, login } = useAuth()
  const router = useRouter()
  const [username, setUsername] = useState("alice")
  const [password, setPassword] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState(false)

  useEffect(() => {
    if (session) router.replace("/incidents")
  }, [session, router])

  async function submit(event: FormEvent) {
    event.preventDefault()
    setPending(true)
    setError(null)
    try {
      await login(username, password)
    } catch (e) {
      setError(
        e instanceof ApiError && e.status === 401 ? "Wrong username or password." : "The platform API is unreachable.",
      )
    } finally {
      setPending(false)
    }
  }

  return (
    <main id="main" className="grid min-h-screen place-items-center px-4">
      <div className="w-full max-w-sm">
        <div className="mb-8 flex items-center gap-2">
          <span className="bg-primary text-primary-foreground grid size-8 place-items-center rounded-md">
            <Radio className="size-4" aria-hidden />
          </span>
          <div>
            <h1 className="text-lg font-semibold tracking-tight">Relay</h1>
            <p className="text-muted-foreground text-xs">Agentic incident response</p>
          </div>
        </div>
        {DEMO ? (
          <div className="bg-card space-y-4 rounded-xl border p-6 text-sm shadow-sm">
            <p>
              A read-only demo: incidents Relay really investigated on its chaos scenarios, with every agent step,
              hypothesis, approval and eval run as the platform recorded them.
            </p>
            <Button className="w-full" onClick={() => void login("visitor", "")}>
              Enter the demo
            </Button>
          </div>
        ) : (
          <form onSubmit={submit} className="bg-card space-y-4 rounded-xl border p-6 shadow-sm">
            <div className="space-y-2">
              <Label htmlFor="username">Username</Label>
              <Input
                id="username"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoComplete="username"
                required
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="password">Password</Label>
              <Input
                id="password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                required
                autoFocus
              />
            </div>
            {error && (
              <p role="alert" className="text-destructive text-sm">
                {error}
              </p>
            )}
            <Button type="submit" className="w-full" disabled={pending}>
              {pending ? "Signing in…" : "Sign in"}
            </Button>
          </form>
        )}
        {!DEMO && (
          <div className="text-muted-foreground mt-4 rounded-lg border border-dashed p-3 text-xs">
            <p className="text-foreground mb-1 font-medium">Demo users (password relay-demo)</p>
            <ul className="space-y-0.5">
              {DEMO_USERS.map((u) => (
                <li key={u.username}>
                  <button
                    type="button"
                    onClick={() => setUsername(u.username)}
                    className="text-foreground font-mono underline-offset-2 hover:underline focus-visible:underline focus-visible:outline-none"
                  >
                    {u.username}
                  </button>{" "}
                  · {u.roles}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </main>
  )
}
