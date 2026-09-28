import type { DeploymentStatus } from "@/graphql/lifecycle/lifecycle.types";

/** Status and tab vocabulary shared by the fleet deployments list and detail. */

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
// one. (The tab split is a server-side filter now — see TAB_FILTERS.)
// IN_FLIGHT is the same set the per-app screen uses.
export { IN_FLIGHT } from "@/components/screens/apps/deployments/app-deployments-format";

export const TERMINAL: ReadonlySet<DeploymentStatus> = new Set([
  "failed",
  "rolled_back",
  "superseded",
]);

// Sub-navigation tabs (#797). Previews and the approval queue are
// subdivisions of the deployment fleet, not separate primitives, so
// they live as tabs here rather than as top-level nav entries. The tab
// replaces the older status-filter pills — the pills were themselves a
// status axis, so stacking both would be redundant.
export type FleetTab = "active" | "previews" | "pending" | "history";

// Observe signal surfaces folded into the fleet tabs (#892). They are
// gateway placeholders into the fleet-wide explorers — they render an
// EmptyState instead of deployment rows, so they carry no badge count.
export type SignalTab = "metrics" | "logs" | "traces";

export type DeploymentTab = FleetTab | SignalTab;

export const DEPLOYMENT_TABS: readonly DeploymentTab[] = [
  "active",
  "previews",
  "pending",
  "history",
  "metrics",
  "logs",
  "traces",
];

export function signalTab(tab: DeploymentTab): SignalTab | null {
  return tab === "metrics" || tab === "logs" || tab === "traces" ? tab : null;
}

export type ActionKind = "approve" | "abort" | "rollback" | "redeploy";

/** Trigger kinds an operator can pick when starting a deployment by hand. */
export const TRIGGER_KINDS = ["manual", "ci", "scheduled", "promotion"] as const;
export type TriggerKind = (typeof TRIGGER_KINDS)[number];
