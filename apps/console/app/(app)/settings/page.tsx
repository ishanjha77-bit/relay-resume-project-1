"use client"

import { Kbd } from "@/components/ui/kbd"
import { API_URL } from "@/lib/api"
import { useAuth } from "@/lib/auth"
import { useLive } from "@/lib/live"

const SHORTCUTS: [string[], string][] = [
  [["Ctrl", "K"], "Command palette: jump to any incident or page"],
  [["G", "I"], "Go to incidents"],
  [["G", "E"], "Go to evals"],
  [["J"], "Next incident in the inbox"],
  [["K"], "Previous incident"],
  [["Enter"], "Open the selected incident"],
]

export default function SettingsPage() {
  const { session } = useAuth()
  const { status } = useLive()
  return (
    <div className="h-full overflow-y-auto">
      <header className="border-b px-6 py-3">
        <h1 className="text-sm font-semibold">Settings</h1>
      </header>
      <div className="max-w-2xl space-y-6 p-6 text-sm">
        <section className="bg-card rounded-lg border p-4">
          <h2 className="text-muted-foreground mb-3 text-xs font-medium">Account</h2>
          <dl className="grid grid-cols-[8rem_1fr] gap-y-2">
            <dt className="text-muted-foreground">Signed in as</dt>
            <dd>
              {session?.user.display_name}{" "}
              <span className="text-muted-foreground font-mono">({session?.user.username})</span>
            </dd>
            <dt className="text-muted-foreground">Roles</dt>
            <dd className="font-mono">{session?.user.roles.join(", ").toLowerCase()}</dd>
          </dl>
        </section>
        <section className="bg-card rounded-lg border p-4">
          <h2 className="text-muted-foreground mb-3 text-xs font-medium">Connection</h2>
          <dl className="grid grid-cols-[8rem_1fr] gap-y-2">
            <dt className="text-muted-foreground">Platform API</dt>
            <dd className="font-mono">{API_URL}</dd>
            <dt className="text-muted-foreground">Live updates</dt>
            <dd>{status}</dd>
          </dl>
        </section>
        <section className="bg-card rounded-lg border p-4">
          <h2 className="text-muted-foreground mb-3 text-xs font-medium">Keyboard</h2>
          <ul className="space-y-2">
            {SHORTCUTS.map(([keys, what]) => (
              <li key={what} className="flex items-center gap-3">
                <span className="flex w-28 gap-1">
                  {keys.map((k) => (
                    <Kbd key={k}>{k}</Kbd>
                  ))}
                </span>
                <span className="text-muted-foreground">{what}</span>
              </li>
            ))}
          </ul>
        </section>
      </div>
    </div>
  )
}
