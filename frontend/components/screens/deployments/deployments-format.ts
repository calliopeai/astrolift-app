import type { DeploymentStatus } from "@/graphql/lifecycle/lifecycle.types";

/** Status vocabulary shared by the fleet deployments list and detail. */

export const statusToDot: Record<DeploymentStatus, "ok" | "warn" | "error" | "muted" | "pending"> =
  {
    pending_approval: "warn",
    pending: "warn",
    deploying: "pending",
    redeploying: "pending",
    running: "ok",
    failed: "error",
    superseded: "muted",
    rolled_back: "muted",
  };

// Status taxonomy used by the bulk-action gating: cancel applies only to
// a uniformly in-flight selection, redeploy only to a uniformly terminal
// one. IN_FLIGHT is the same set the per-app screen uses.
export { IN_FLIGHT } from "@/components/screens/apps/deployments/app-deployments-format";

export const TERMINAL: ReadonlySet<DeploymentStatus> = new Set([
  "failed",
  "rolled_back",
  "superseded",
]);

export type ActionKind = "approve" | "abort" | "rollback" | "redeploy";

/** Trigger kinds an operator can pick when starting a deployment by hand. */
export const TRIGGER_KINDS = ["manual", "ci", "scheduled", "promotion"] as const;
export type TriggerKind = (typeof TRIGGER_KINDS)[number];
