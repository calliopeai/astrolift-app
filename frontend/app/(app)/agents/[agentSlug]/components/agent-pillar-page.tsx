"use client";

import { AgentDetailShell } from "./agent-detail-shell";
import { BuildContent } from "./build-content";
import { ObserveContent } from "./observe-content";
import { SecureContent } from "./secure-content";
import { TabStub } from "./tab-stub";

export type AgentPillar = "build" | "run" | "observe" | "control" | "secure";

/**
 * Single client entry for every `/agents/[agentSlug]/<pillar>` route. The
 * server `page.tsx` resolves the slug and names its pillar; this renders the
 * shared `AgentDetailShell` (header + `AgentTabs`) and slots the matching
 * content. Build + Observe are real (spec 33 PR-9); Run is the PR-10 stub,
 * Control the PR-11/12 stub, Secure the Zentinelle gate.
 */
export function AgentPillarPage({
  agentSlug,
  pillar,
}: {
  agentSlug: string;
  pillar: AgentPillar;
}) {
  return (
    <AgentDetailShell agentSlug={agentSlug}>
      {({ agent, orgId }) => {
        switch (pillar) {
          case "build":
            return <BuildContent agent={agent} />;
          case "observe":
            return <ObserveContent orgId={orgId} />;
          case "secure":
            return <SecureContent agentName={agent.name} />;
          case "run":
            return (
              <TabStub
                title="Run — coming in PR-10"
                description="Dispatch-now (Once) and the per-agent Executions list land in the next PR. You'll trigger a run here and watch it appear with a live status badge."
              />
            );
          case "control":
            return (
              <TabStub
                title="Control — coming in PR-11"
                description="The run-spec editor (Once / Schedule / Service, then Loop / Trigger / scaled-scaling) lands in a later PR. You'll edit how and when this agent runs here."
              />
            );
          default:
            return null;
        }
      }}
    </AgentDetailShell>
  );
}
