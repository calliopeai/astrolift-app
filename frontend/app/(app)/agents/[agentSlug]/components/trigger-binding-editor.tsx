"use client";

import { TriggerBindingEditorView } from "@/components/screens/agents/detail/TriggerBindingEditor";
import { useTriggerBindings } from "@/components/screens/agents/detail/use-trigger-bindings";

/**
 * The Control tab's trigger-binding editor. The view owns the markup; this
 * container runs its data hook only when Trigger mode renders it.
 */
export function TriggerBindingEditor(props: {
  agentSlug: string;
  agentName: string;
  orgId: string;
}) {
  return <TriggerBindingEditorView {...useTriggerBindings(props)} />;
}
