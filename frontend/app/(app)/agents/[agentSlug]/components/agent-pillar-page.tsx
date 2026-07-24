"use client";

import { AgentDetailShell } from "./agent-detail-shell";
import { BuildContent } from "./build-content";
import { ControlContent } from "./control-content";
import { ObserveContent } from "./observe-content";
import { OverviewContent } from "./overview-content";
import { RunContent } from "./run-content";
import { SecureContent } from "./secure-content";

export type AgentPillar = "overview" | "build" | "run" | "observe" | "control" | "secure";

/**
 * Single client entry for every `/agents/[agentSlug]/<pillar>` route. The
 * server `page.tsx` resolves the slug and names its pillar; this renders the
 * shared `AgentDetailShell` (header + `AgentTabs`) and slots the matching
 * content. Overview is the agent-native landing pillar; Build + Observe + Run
 * + Control are real; Control is the run-spec editor (Once/Schedule/Service in
 * PR-11; Loop/Trigger/scaled-scaling in PR-12). Secure is the Zentinelle gate.
 */
export function AgentPillarPage({ agentSlug, pillar }: { agentSlug: string; pillar: AgentPillar }) {
  return (
    <AgentDetailShell agentSlug={agentSlug}>
      {({ agent, orgId }) => {
        switch (pillar) {
          case "overview":
            return <OverviewContent agent={agent} orgId={orgId} />;
          case "build":
            return <BuildContent agent={agent} orgId={orgId} />;
          case "observe":
            return <ObserveContent orgId={orgId} />;
          case "secure":
            return <SecureContent agentName={agent.name} />;
          case "run":
            return <RunContent agent={agent} orgId={orgId} />;
          case "control":
            return <ControlContent agent={agent} />;
          default:
            return null;
        }
      }}
    </AgentDetailShell>
  );
}
