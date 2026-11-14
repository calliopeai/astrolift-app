import { StatusDot as LibraryStatusDot } from "@calliopelabs/ui";

type Status = "ok" | "warn" | "error" | "muted" | "pending";

/**
 * The library StatusDot under the app's `status` names. `pending` is the
 * in-flight state: the info tone, pulsing. Colours are status tokens, so
 * "healthy / active / running" reads the same whatever accent is chosen.
 */
export function StatusDot({ status, className }: { status: Status; className?: string }) {
  return status === "pending" ? (
    <LibraryStatusDot tone="info" pulse className={className} />
  ) : (
    <LibraryStatusDot tone={status} className={className} />
  );
}
