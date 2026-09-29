"use client";

import { useWorkflowSettings } from "@/components/screens/workflows/detail/use-workflow-settings";
import { WorkflowSettingsView } from "@/components/screens/workflows/detail/WorkflowSettings";

import { useFramedWorkflow } from "./framed-workflow";

/** The Settings tab: General and the Danger zone. */
export function SettingsContent() {
  const framed = useFramedWorkflow();
  const settings = useWorkflowSettings(framed);
  // Keyed on the saved values, so a save or a refetch resets the form to them.
  return (
    <WorkflowSettingsView
      key={`${settings.general.slug}:${settings.general.name}:${settings.general.description}`}
      {...settings}
    />
  );
}
