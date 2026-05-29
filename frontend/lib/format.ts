/**
 * Format an ISO timestamp as a human-friendly relative age
 * (``5m ago`` / ``3h ago`` / ``2d ago``). Falls back to the absolute
 * date once the delta exceeds 30 days so the cue stays useful for
 * dormant rows. Returns an empty string when the input does not parse;
 * callers should guard on the source value separately.
 */
export function formatRelativeAge(iso: string): string {
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return "";
  const seconds = Math.max(0, (Date.now() - then) / 1000);
  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone:
      typeof window !== "undefined"
        ? Intl.DateTimeFormat().resolvedOptions().timeZone
        : "UTC",
  }).format(new Date(then));
}
