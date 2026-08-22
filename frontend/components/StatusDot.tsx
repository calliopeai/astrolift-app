import { cn } from "@/lib/utils";

type Status = "ok" | "warn" | "error" | "muted" | "pending";

// `ok` uses the Astrolift action green so that "healthy / active / running"
// reads as the brand-positive state across the app. Failure states stay red so
// they still register as alarming. The glow here is deliberate: status dots and
// focus rings are the only places the brand permits one.
const statusClass: Record<Status, string> = {
  ok: "bg-[var(--brand-primary)] shadow-[color:var(--brand-primary)]/50",
  warn: "bg-warning shadow-warning/50",
  error: "bg-danger shadow-danger/50",
  muted: "bg-muted-foreground shadow-muted-foreground/40",
  pending: "bg-info shadow-info/50 animate-pulse",
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
