"use client";

import { useState } from "react";
import { toast } from "sonner";

import { useConfirm } from "@/hooks/use-confirm";
import {
  useCancelWorkflowInstance,
  useTerminateWorkflowInstance,
  useWorkflowInstanceDetail,
  useWorkflowInstances,
} from "@/graphql/workflows/workflows.hooks";

/**
 * The Temporal instance list behind WorkflowInstancesPanelView (#437):
 * the controlled filters and the status-filtered instance query.
 */
export function useWorkflowInstancesPanel({
  workflowType,
  initialStatus = "ALL",
  isAdmin = true,
}: {
  workflowType?: string;
  initialStatus?: string;
  isAdmin?: boolean;
}) {
  const [typeFilter, setTypeFilter] = useState(workflowType ?? "");
  const [statusFilter, setStatusFilter] = useState(initialStatus);
  const [selectedWorkflowId, setSelectedWorkflowId] = useState<string | null>(null);

  const { instances, loading, error, refetch } = useWorkflowInstances({
    workflowType: typeFilter.trim() || null,
    status: statusFilter === "ALL" ? null : statusFilter,
    limit: 50,
  });

  return {
    typeFilter,
    setTypeFilter,
    statusFilter,
    setStatusFilter,
    selectedWorkflowId,
    setSelectedWorkflowId,
    instances,
    loading,
    error,
    refetch: () => {
      refetch();
    },
    isAdmin,
  };
}

/** One instance's detail and activity feed, for InstanceDetailView. */
export function useWorkflowInstanceDetailPanel(workflowId: string | null) {
  const { detail, loading, error, refetch } = useWorkflowInstanceDetail(workflowId);
  return {
    workflowId,
    detail,
    loading,
    error,
    refetch: () => {
      refetch();
    },
  };
}

/**
 * Cancel / terminate for a running instance, for InstanceAdminControlsView.
 * The admin-only check happens on the resolver.
 */
export function useInstanceAdminControls(workflowId: string, onAfter: () => void) {
  const confirm = useConfirm();
  const [cancelMutation, { loading: cancelLoading }] = useCancelWorkflowInstance();
  const [terminateMutation, { loading: terminateLoading }] = useTerminateWorkflowInstance();

  const onCancel = async () => {
    const ok = await confirm({
      title: "Cancel workflow?",
      description:
        "Sends a cooperative cancel signal. The workflow may run cleanup before exiting.",
      confirmLabel: "Cancel workflow",
      cancelLabel: "Keep running",
    });
    if (!ok) return;
    const { data } = await cancelMutation({ variables: { workflowId } });
    if (data?.cancelWorkflowInstance?.ok) {
      toast.success("Cancel signal sent");
      onAfter();
    } else {
      const msg = data?.cancelWorkflowInstance?.errors?.[0]?.messages?.[0] ?? "Cancel failed";
      toast.error(msg);
    }
  };

  const onTerminate = async () => {
    const ok = await confirm({
      title: "Terminate workflow?",
      description:
        "Hard kill — no cleanup runs. Reserve for wedged workflows the cooperative cancel can't unstick.",
      confirmLabel: "Terminate",
      cancelLabel: "Keep running",
    });
    if (!ok) return;
    const reason = window.prompt("Termination reason (required):");
    if (!reason || !reason.trim()) {
      toast.error("Termination reason is required");
      return;
    }
    const { data } = await terminateMutation({
      variables: { workflowId, reason: reason.trim() },
    });
    if (data?.terminateWorkflowInstance?.ok) {
      toast.success("Workflow terminated");
      onAfter();
    } else {
      const msg = data?.terminateWorkflowInstance?.errors?.[0]?.messages?.[0] ?? "Terminate failed";
      toast.error(msg);
    }
  };

  return { onCancel, onTerminate, cancelLoading, terminateLoading };
}
