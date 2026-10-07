/** Single-key shortcuts must not fire while the user is typing. */
export function isTyping(event: KeyboardEvent): boolean {
  const target = event.target as HTMLElement | null
  return Boolean(target && (target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)))
}
