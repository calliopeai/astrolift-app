"use client";

import { RunAgentScreen } from "@/components/screens/agents/runs/RunAgentScreen";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";

import { DispatchTab } from "../../dispatch-tab";

/** The Run agent page: the dispatch form in its page frame, once the org is known. */
export function RunAgentClient({ agentSlug }: { agentSlug: string }) {
  const { org } = useActiveOrg();
  return (
    <RunAgentScreen>
      {org?.id ? <DispatchTab orgId={org.id} defaultAgentSlug={agentSlug} /> : null}
    </RunAgentScreen>
  );
}
