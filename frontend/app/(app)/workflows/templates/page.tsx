"use client";

import { WorkflowTemplatesScreen } from "@/components/screens/workflows/list/WorkflowTemplatesScreen";
import { useWorkflowTemplates } from "@/components/screens/workflows/list/use-workflow-templates";

export default function WorkflowTemplatesPage() {
  return <WorkflowTemplatesScreen {...useWorkflowTemplates()} />;
}
