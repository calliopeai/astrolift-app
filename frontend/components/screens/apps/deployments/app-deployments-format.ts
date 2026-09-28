import type { DeploymentStatus } from "@/graphql/lifecycle/lifecycle.types";

/** Shared formatters for the app deployments screens. */

export const IN_FLIGHT: ReadonlySet<DeploymentStatus> = new Set([
  "pending_approval",
  "pending",
  "deploying",
  "redeploying",
]);

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null) return "—";
  if (seconds < 60) return `${seconds}s`;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}m ${s}s`;
}

export function formatTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString();
}
