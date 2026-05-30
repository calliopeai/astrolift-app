import { AgentsClient } from "./agents-client";

export const metadata = { title: "Agents · Astrolift" };

/**
 * Agents — dispatch and monitor AI agent workloads across the fleet.
 *
 * Agents are first-class runtime primitives alongside apps and workflows.
 * They run inside the same EKS clusters, share the same deployment
 * pipeline, and surface here as a dedicated fleet view with four tabs:
 * Active runs, Dispatch (trigger a new run), History, and Registry
 * (agent-kind workloads declared in app manifests).
 */
export default function AgentsPage() {
  return <AgentsClient />;
}
