import { DOT_TONE, IN_FLIGHT } from "@/lib/status-tones";
import { cn } from "@/lib/utils";

type Status = "ok" | "warn" | "error" | "muted" | "pending";

// Colours come from the one status map (lib/status-tones), so "healthy /
// active / running" reads the same whatever accent the operator picked.
// `pending` is the in-flight state: the info tone, pulsing.
const statusClass: Record<Status, string> = {
  ok: DOT_TONE.ok,
  warn: DOT_TONE.warn,
  error: DOT_TONE.error,
  muted: DOT_TONE.muted,
  pending: cn(DOT_TONE.info, IN_FLIGHT),
};

export function StatusDot({ status, className }: { status: Status; className?: string }) {
  return (
    <span
      className={cn(
        "inline-block size-2 shrink-0 rounded-full shadow-[0_0_0_2px_var(--background)]",
        statusClass[status],
        className
      )}
    />
  );
}
