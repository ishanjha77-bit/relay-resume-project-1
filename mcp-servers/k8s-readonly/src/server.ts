/**
 * Relay's Kubernetes MCP server (TypeScript SDK), read-only.
 *
 * Like the Python servers (mcp-servers/mcp-kit): Streamable HTTP in stateless
 * JSON mode, a bearer token checked in constant time, DNS-rebinding protection
 * with an explicit Host allowlist, /healthz for probes, JSON logs.
 */
import { timingSafeEqual } from "node:crypto"
import { createServer, type IncomingMessage, type ServerResponse } from "node:http"

import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js"
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js"
import { z } from "zod"

import { toJson } from "./shape.ts"
import { K8sTools } from "./tools.ts"

const SERVICE = "k8s-readonly"
const READ_ONLY = { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false }

export function buildServer(tools: K8sTools): McpServer {
  const mcp = new McpServer(
    { name: SERVICE, version: "0.1.0" },
    {
      instructions:
        `Read-only view of Kubernetes namespace "${tools.namespace}": deployments, rollouts, pods and events. ` +
        "Use it to answer what changed and when (rollout_history), which pods are unwell and why " +
        "(pod_status, recent_events), and how a service is configured (deployment_config).",
    },
  )
  const json = async (work: () => Promise<unknown>) => {
    try {
      return { content: [{ type: "text" as const, text: toJson(await work()) }] }
    } catch (error) {
      return { content: [{ type: "text" as const, text: String(error instanceof Error ? error.message : error) }], isError: true }
    }
  }
  const service = z.string().describe('Service (deployment) name, e.g. "orders".')

  mcp.registerTool(
    "list_deployments",
    { description: "Every deployment in the namespace with its version, image and ready/unavailable replicas.", annotations: READ_ONLY },
    () => json(() => tools.listDeployments()),
  )
  mcp.registerTool(
    "rollout_history",
    {
      description:
        "A service's rollout history, newest first: revision, version, image, change-cause, when, and what changed " +
        "from the previous revision (image and environment). Use it to find a bad deploy or config change.",
      inputSchema: { service, limit: z.number().int().min(1).max(10).default(5).describe("Revisions to return.") },
      annotations: READ_ONLY,
    },
    ({ service, limit }) => json(() => tools.rolloutHistory(service, limit)),
  )
  mcp.registerTool(
    "pod_status",
    {
      description:
        "Pods with phase, readiness, restart count, waiting reason (e.g. CrashLoopBackOff) and how the last container " +
        "ended (e.g. OOMKilled, exit code). Omit service for every pod.",
      inputSchema: { service: service.optional() },
      annotations: READ_ONLY,
    },
    ({ service }) => json(() => tools.podStatus(service)),
  )
  mcp.registerTool(
    "recent_events",
    {
      description:
        "Recent Kubernetes events, newest first: OOM kills, failed probes, back-offs, scheduling and image problems. " +
        "Warnings only by default.",
      inputSchema: {
        service: service.optional(),
        minutes: z.number().int().min(1).max(360).default(30).describe("How far back to look."),
        warnings_only: z.boolean().default(true),
      },
      annotations: READ_ONLY,
    },
    ({ service, minutes, warnings_only }) => json(() => tools.recentEvents(service, minutes, warnings_only)),
  )
  mcp.registerTool(
    "deployment_config",
    {
      description: "How a service runs: image, replicas, resource requests/limits, probes and environment (secrets masked).",
      inputSchema: { service },
      annotations: READ_ONLY,
    },
    ({ service }) => json(() => tools.deploymentConfig(service)),
  )
  return mcp
}

/** Host header allowlist; "host:*" allows any port. */
export function hostAllowed(host: string | undefined, allowed: string[]): boolean {
  if (!host) return false
  const [name] = host.split(":")
  return allowed.some((rule) => rule === host || (rule.endsWith(":*") && rule.slice(0, -2) === name))
}

export function tokenMatches(header: string | undefined, token: string): boolean {
  const expected = Buffer.from(`Bearer ${token}`)
  const given = Buffer.from(header ?? "")
  return token.length > 0 && given.length === expected.length && timingSafeEqual(given, expected)
}

function log(level: string, message: string) {
  process.stdout.write(`${JSON.stringify({ "@timestamp": new Date().toISOString(), level, message, service: SERVICE })}\n`)
}

async function body(req: IncomingMessage): Promise<unknown> {
  const chunks: Buffer[] = []
  for await (const chunk of req) chunks.push(chunk as Buffer)
  const text = Buffer.concat(chunks).toString("utf8")
  return text ? JSON.parse(text) : undefined
}

function reply(res: ServerResponse, status: number, text: string, headers: Record<string, string> = {}) {
  res.writeHead(status, { "content-type": "text/plain", ...headers }).end(text)
}

export function start(tools: K8sTools, options: { port: number; token: string; allowedHosts: string[] }) {
  const server = createServer(async (req, res) => {
    const path = new URL(req.url ?? "/", "http://localhost").pathname
    if (path === "/healthz") return reply(res, 200, "ok")
    if (!hostAllowed(req.headers.host, options.allowedHosts)) return reply(res, 421, "unexpected Host header")
    if (path !== "/mcp") return reply(res, 404, "not found")
    if (!tokenMatches(req.headers.authorization, options.token)) {
      return reply(res, 401, "missing or wrong bearer token", { "www-authenticate": 'Bearer realm="relay"' })
    }
    // Stateless: a fresh server and transport per request, so any replica can serve any call.
    const mcp = buildServer(tools)
    const transport = new StreamableHTTPServerTransport({ sessionIdGenerator: undefined, enableJsonResponse: true })
    res.on("close", () => {
      void transport.close()
      void mcp.close()
    })
    try {
      await mcp.connect(transport)
      await transport.handleRequest(req, res, req.method === "POST" ? await body(req) : undefined)
    } catch (error) {
      log("ERROR", `request failed: ${error instanceof Error ? error.message : String(error)}`)
      if (!res.headersSent) reply(res, 500, "internal error")
    }
  })
  server.listen(options.port, () => log("INFO", `listening on :${options.port}, namespace ${tools.namespace}`))
  return server
}
