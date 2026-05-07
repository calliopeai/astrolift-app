import { cn } from "@/lib/utils";

type Status = "ok" | "warn" | "error" | "muted" | "pending";

// `ok` uses Astrolift brand teal so that "healthy / active / running" reads
// as the brand-positive state across the app. Failure states stay red so
// they still register as alarming. Warn / pending stay amber / blue.
const statusClass: Record<Status, string> = {
  ok: "bg-[var(--brand-primary)] shadow-[color:var(--brand-primary)]/50",
  warn: "bg-amber-500 shadow-amber-500/50",
  error: "bg-red-500 shadow-red-500/50",
  muted: "bg-zinc-400 shadow-zinc-400/40",
  pending: "bg-blue-500 shadow-blue-500/50 animate-pulse",
};

export function StatusDot({ status, className }: { status: Status; className?: string }) {
  return (
    <span
      className={cn(
        "inline-block size-2 rounded-full shadow-[0_0_0_2px_var(--background)]",
        statusClass[status],
        className
      )}
    />
  );
}
