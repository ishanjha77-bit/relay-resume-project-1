"use client"

import { useQuery } from "@tanstack/react-query"
import { BarChart3, Inbox, LogOut, Settings } from "lucide-react"
import { useRouter } from "next/navigation"
import { useEffect, useState } from "react"

import { StatusPill } from "@/components/status"
import {
  Command,
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
  CommandShortcut,
} from "@/components/ui/command"
import { api } from "@/lib/api"
import { useAuth } from "@/lib/auth"

/** ⌘K / Ctrl+K: jump to any incident or page. */
export function CommandPalette() {
  const [open, setOpen] = useState(false)
  const router = useRouter()
  const { logout } = useAuth()
  const { data } = useQuery({ queryKey: ["incidents", "all"], queryFn: () => api.incidents("all"), enabled: open })

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key.toLowerCase() === "k" && (event.metaKey || event.ctrlKey)) {
        event.preventDefault()
        setOpen((value) => !value)
      }
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [])

  const go = (href: string) => {
    setOpen(false)
    router.push(href)
  }

  return (
    <CommandDialog
      open={open}
      onOpenChange={setOpen}
      title="Command palette"
      description="Jump to an incident or a page"
    >
      {/* This CommandDialog renders its children as-is: the cmdk root goes here. */}
      <Command>
        <CommandInput placeholder="Search incidents, pages…" />
        <CommandList>
          <CommandEmpty>Nothing matches.</CommandEmpty>
          <CommandGroup heading="Pages">
            <CommandItem onSelect={() => go("/incidents")}>
              <Inbox /> Incidents <CommandShortcut>G I</CommandShortcut>
            </CommandItem>
            <CommandItem onSelect={() => go("/evals")}>
              <BarChart3 /> Evals <CommandShortcut>G E</CommandShortcut>
            </CommandItem>
            <CommandItem onSelect={() => go("/settings")}>
              <Settings /> Settings
            </CommandItem>
          </CommandGroup>
          {data && data.items.length > 0 && (
            <>
              <CommandSeparator />
              <CommandGroup heading="Incidents">
                {data.items.slice(0, 50).map((incident) => (
                  <CommandItem
                    key={incident.id}
                    value={`${incident.key} ${incident.title} ${incident.service ?? ""}`}
                    onSelect={() => go(`/incidents/${incident.id}`)}
                  >
                    <span className="text-muted-foreground shrink-0 font-mono text-xs whitespace-nowrap">
                      {incident.key}
                    </span>
                    <span className="truncate">{incident.title}</span>
                    <StatusPill status={incident.status} className="ml-auto" />
                  </CommandItem>
                ))}
              </CommandGroup>
            </>
          )}
          <CommandSeparator />
          <CommandGroup heading="Session">
            <CommandItem
              onSelect={() => {
                setOpen(false)
                logout()
              }}
            >
              <LogOut /> Sign out
            </CommandItem>
          </CommandGroup>
        </CommandList>
      </Command>
    </CommandDialog>
  )
}
