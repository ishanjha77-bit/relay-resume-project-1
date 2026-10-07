const MINUTE = 60
const HOUR = 60 * MINUTE
const DAY = 24 * HOUR

/** "just now", "4m", "3h", "2d": compact ages for dense lists. */
export function age(iso: string, now: number = Date.now()): string {
  const seconds = Math.max(0, (now - new Date(iso).getTime()) / 1000)
  if (seconds < 45) return "just now"
  if (seconds < HOUR) return `${Math.round(seconds / MINUTE)}m`
  if (seconds < DAY) return `${Math.round(seconds / HOUR)}h`
  return `${Math.round(seconds / DAY)}d`
}

export function clock(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })
}

export function dateTime(iso: string): string {
  return new Date(iso).toLocaleString([], { dateStyle: "medium", timeStyle: "short" })
}

/** 75 -> "1m 15s"; 3725 -> "1h 2m". */
export function duration(seconds: number | null | undefined): string {
  if (seconds == null) return "n/a"
  const s = Math.round(seconds)
  if (s < MINUTE) return `${s}s`
  if (s < HOUR) return `${Math.floor(s / MINUTE)}m ${s % MINUTE}s`
  return `${Math.floor(s / HOUR)}h ${Math.floor((s % HOUR) / MINUTE)}m`
}

export function usd(value: number | null | undefined, digits = 4): string {
  return value == null ? "n/a" : `$${value.toFixed(digits)}`
}

export function percent(value: number | null | undefined): string {
  return value == null ? "n/a" : `${Math.round(value * 100)}%`
}

export function tokens(value: number | null | undefined): string {
  if (value == null) return "n/a"
  return value >= 1000 ? `${(value / 1000).toFixed(1)}k` : String(value)
}

/** db_pool_exhaustion -> "DB pool exhaustion". */
export function category(value: string | null | undefined): string {
  if (!value) return "unknown"
  const text = value.replaceAll("_", " ")
  return (text.charAt(0).toUpperCase() + text.slice(1)).replace(/^Db /, "DB ").replace(/ db /, " DB ")
}
