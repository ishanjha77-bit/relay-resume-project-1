import { clusterKube } from "./kube.ts"
import { start } from "./server.ts"
import { K8sTools } from "./tools.ts"

const hosts = process.env.MCP_ALLOWED_HOSTS ?? "localhost:*,127.0.0.1:*"
start(new K8sTools(clusterKube(), process.env.SANDBOX_NAMESPACE ?? "sandbox"), {
  port: Number(process.env.PORT ?? 8000),
  token: process.env.MCP_TOKEN ?? "",
  allowedHosts: hosts.split(",").map((h) => h.trim()).filter(Boolean),
})
