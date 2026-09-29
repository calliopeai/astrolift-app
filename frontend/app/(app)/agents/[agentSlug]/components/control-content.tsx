"use client";

import { TriggerBindingEditor } from "@/app/(app)/agents/[agentSlug]/components/trigger-binding-editor";
import { AgentControlScreen } from "@/components/screens/agents/detail/AgentControl";
import { useAgentControl } from "@/components/screens/agents/detail/use-agent-control";

import { useFramedAgent } from "./framed-agent";

/**
 * Configuration › Run mode & triggers: the run-spec editor (spec 33 PR-11 /
 * PR-12). The screen owns the editor; the Trigger binding editor is passed in
 * as a slot so its query runs only when Trigger mode is shown.
 */
export function ControlContent() {
  const { agent } = useFramedAgent();
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
