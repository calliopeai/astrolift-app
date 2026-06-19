import { AgentsClient } from "./agents-client";

export const metadata = { title: "Agents · Astrolift" };

/**
 * Agents — dispatch and monitor AI agent workloads across the fleet.
 *
 * Agents are first-class runtime primitives alongside apps and workflows.
 * They run inside the same EKS clusters, share the same deployment
 * pipeline, and surface here as a dedicated fleet view with four tabs:
 * Active runs, Dispatch (trigger a new run), History, and Registry — the
 * registered-agents list, scoped to a project with a fleet/all-agents
 * toggle and per-agent live status.
 */
export default function AgentsPage() {
  return <AgentsClient />;
}
