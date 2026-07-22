import { PageShell } from "@/components/PageShell";

import { FleetMapClient } from "./fleet-map-client";

export const metadata = { title: "Fleet map · Astrolift" };

/**
 * /fleet/map — the live agent-fleet dispatch map (#1091, LiveFlowMap P2).
 *
 * A wide-shot view of the dispatch pipeline: the dispatch service and its
 * dispatchers route queued AgentTasks onto clusters, where agent pods run.
 * Built on the same telemetry FlowGraph substrate as the workflow-run DAG
 * (P1, #1090); poll-driven, with edges pulsing per task state transition.
 */
export default function FleetMapPage() {
  return (
    <PageShell
      title="Fleet map"
      description="Live view of the agent-fleet dispatch pipeline — dispatchers route queued tasks onto clusters, where agent pods run. Edges pulse as tasks change state."
    >
      <FleetMapClient />
    </PageShell>
  );
}
