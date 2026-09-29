"use client";

import { WorkflowsListScreen } from "@/components/screens/workflows/list/WorkflowsListScreen";
import { useWorkflowsList } from "@/components/screens/workflows/list/use-workflows-list";

import { WorkflowInstancesPanel } from "./instances-panel";

export default function WorkflowsPage() {
  const list = useWorkflowsList();
  return (
    <WorkflowsListScreen
      {...list}
      instancesPanel={
        <WorkflowInstancesPanel
          workflowType=""
          initialStatus="RUNNING"
          isAdmin={list.entitlement.canRun}
        />
      }
    />
  );
}
