import { BASE_PATH, DEMO, demoFile, demoFilter, VISITOR } from "@/lib/demo"
import { sessionStore } from "@/lib/session"
import type {
  AgentStep,
  Approval,
  FeedbackVote,
  EvalBatch,
  EvalRun,
  IncidentDetail,
  IncidentPage,
  TokenResponse,
  User,
} from "@/lib/types"

/** The platform API, as the browser reaches it (kind publishes it on localhost:8081). */
export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8081"

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message)
  }
}

/** The read-only demo: GETs from the recorded files, every change refused. */
async function demoRequest<T>(path: string, init: RequestInit): Promise<T> {
  if (path === "/api/auth/token") {
    const { user } = VISITOR
    return { access_token: VISITOR.token, token_type: "Bearer", expires_in: 86_400, ...user } as T
  }
  if ((init.method ?? "GET") !== "GET") {
    throw new ApiError(403, "This is a read-only demo. Run Relay locally to approve, reject or resolve.")
  }
  const file = demoFile(path)
  const response = file ? await fetch(`${BASE_PATH}/demo/${file}`) : null
  if (!response?.ok) throw new ApiError(404, "Not part of the demo.")
  return demoFilter(path, await response.json()) as T
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  if (DEMO) return demoRequest<T>(path, init)
  const token = sessionStore.token()
  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: {
      Accept: "application/json",
      ...(init.body ? { "Content-Type": "application/json" } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...init.headers,
    },
  })
  if (response.status === 401 && token) {
    sessionStore.set(null) // expired or revoked: back to the login page
  }
  if (!response.ok) {
    let message = response.statusText
    try {
      const body = await response.json()
      message = body.detail ?? body.message ?? message
    } catch {
      // not JSON: keep the status text
    }
    throw new ApiError(response.status, message)
  }
  return (response.status === 204 ? undefined : await response.json()) as T
}

export const api = {
  login: (username: string, password: string) =>
    request<TokenResponse>("/api/auth/token", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),
  me: () => request<User>("/api/auth/me"),
  incidents: (status: "active" | "resolved" | "all", cursor?: string) =>
    request<IncidentPage>(
      `/api/incidents?status=${status}&limit=100${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`,
    ),
  incident: (id: string) => request<IncidentDetail>(`/api/incidents/${id}`),
  steps: (id: string) => request<AgentStep[]>(`/api/incidents/${id}/steps?limit=1000`),
  resolve: (id: string) => request<void>(`/api/incidents/${id}/resolve`, { method: "POST" }),
  decide: (incidentId: string, approvalId: string, decision: "approve" | "reject", reason?: string) =>
    request<Approval>(`/api/incidents/${incidentId}/approvals/${approvalId}`, {
      method: "POST",
      body: JSON.stringify({ decision, reason: reason || null }),
    }),
  feedback: (incidentId: string, vote: FeedbackVote) =>
    request<void>(`/api/incidents/${incidentId}/feedback`, { method: "POST", body: JSON.stringify(vote) }),
  evalBatches: () => request<EvalBatch[]>("/api/evals/batches"),
  evalRuns: (batch?: string) =>
    request<EvalRun[]>(`/api/evals/runs?limit=1000${batch ? `&batch=${encodeURIComponent(batch)}` : ""}`),
}
