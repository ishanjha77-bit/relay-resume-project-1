/**
 * Tool output shaping, as in the Python servers (mcp-kit): compact JSON,
 * secrets masked before anything leaves the server, and a size cap.
 */

export const MAX_CHARS = 16_000

const SECRET_NAME = /(pass(word)?|secret|token|api[_-]?key|credential|private[_-]?key|auth)/i
const URL_CREDENTIALS = /\/\/([^:/@\s]+):([^@\s]+)@/g

/** An environment value, unless its name or shape says it is a secret. */
export function redactEnv(name: string, value: string | undefined): string | undefined {
  if (value === undefined) return undefined
  if (SECRET_NAME.test(name)) return "[redacted]"
  return value.replace(URL_CREDENTIALS, "//$1:[redacted]@")
}

/** Compact JSON, clipped to `limit` characters with a note saying so. */
export function toJson(value: unknown, limit = MAX_CHARS): string {
  const text = JSON.stringify(value)
  if (text.length <= limit) return text
  return `${text.slice(0, limit)}… [clipped: ${text.length - limit} more characters; narrow the query]`
}

/** Seconds or minutes ago, from an ISO time or Date. */
export function ago(when: Date | string | undefined, now: Date): string | undefined {
  if (!when) return undefined
  const seconds = Math.round((now.getTime() - new Date(when).getTime()) / 1000)
  if (seconds < 120) return `${seconds}s ago`
  if (seconds < 7200) return `${Math.round(seconds / 60)}m ago`
  return `${Math.round(seconds / 3600)}h ago`
}
