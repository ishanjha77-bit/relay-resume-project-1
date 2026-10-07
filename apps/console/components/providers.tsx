"use client"

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { ThemeProvider } from "next-themes"
import type { ReactNode } from "react"

import { Toaster } from "@/components/ui/sonner"
import { TooltipProvider } from "@/components/ui/tooltip"
import { AuthProvider } from "@/lib/auth"
import { LiveProvider } from "@/lib/live"

let browserQueryClient: QueryClient | undefined

function getQueryClient() {
  const make = () =>
    new QueryClient({
      defaultOptions: {
        queries: { staleTime: 15_000, refetchOnWindowFocus: true, retry: 1 },
      },
    })
  // Keep server renders isolated and the browser cache across renders.
  if (typeof window === "undefined") return make()
  browserQueryClient ??= make()
  return browserQueryClient
}

export function Providers({ children }: { children: ReactNode }) {
  return (
    <ThemeProvider attribute="class" defaultTheme="dark" enableSystem={false} disableTransitionOnChange>
      <QueryClientProvider client={getQueryClient()}>
        <AuthProvider>
          <LiveProvider>
            <TooltipProvider delayDuration={300}>
              {children}
              <Toaster position="bottom-right" />
            </TooltipProvider>
          </LiveProvider>
        </AuthProvider>
      </QueryClientProvider>
    </ThemeProvider>
  )
}
