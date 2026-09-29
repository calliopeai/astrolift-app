// Mirror of the (unexported) StatusDot `Status` union.
type StatusDotStatus = "ok" | "warn" | "error" | "muted" | "pending";

// Run vocabulary (ScheduledJobRun / TaskRun) → status-dot tone. Backend states
// vary in case (pending/running/succeeded/failed/…); normalize + degrade
// unknown values to a muted dot.
const RUN_DOT: Record<string, StatusDotStatus> = {
  succeeded: "ok",
  completed: "ok",
  running: "pending",
  active: "pending",
  pending: "warn",
  queued: "warn",
  failed: "error",
  error: "error",
  timed_out: "error",
  cancelled: "muted",
  canceled: "muted",
};

export function runStatusDot(status: string): StatusDotStatus {
  return RUN_DOT[status.toLowerCase()] ?? "muted";
}

export function titleCaseStatus(status: string): string {
  if (!status) return "—";
  return status
    .replace(/[_-]+/g, " ")
    .split(" ")
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase())
    .join(" ");
}

/** Human duration from a seconds count (nullable). */
export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null || seconds < 0) return "—";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  if (m < 60) return s ? `${m}m ${s}s` : `${m}m`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}
