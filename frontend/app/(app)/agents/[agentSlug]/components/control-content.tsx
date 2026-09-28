"use client";

import { TriggerBindingEditor } from "@/app/(app)/agents/[agentSlug]/components/trigger-binding-editor";
import { AgentControlScreen } from "@/components/screens/agents/detail/AgentControl";
import { useAgentControl } from "@/components/screens/agents/detail/use-agent-control";
import type { AstroliftAgentListItem } from "@/graphql/agents/agents.types";

/**
 * Control tab for an agent: the run-spec editor (spec 33 PR-11 / PR-12). The
 * screen owns the editor; the Trigger binding editor is passed in as a slot so
 * its query runs only when Trigger mode is shown.
 */
export function ControlContent({ agent }: { agent: AstroliftAgentListItem }) {
  const control = useAgentControl(agent);
  return (
    <AgentControlScreen
      {...control}
      triggerEditor={
        <TriggerBindingEditor agentSlug={agent.slug} agentName={agent.name} orgId={control.orgId} />
      }
    />
  );
}
