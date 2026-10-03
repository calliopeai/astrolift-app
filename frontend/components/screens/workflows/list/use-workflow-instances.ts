"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { toast } from "sonner";
import { useEffect, useRef } from "react";

import { useListState } from "@/components/list/use-list-state";
import { useDebounce } from "@/hooks/use-debounce";
import { useConfirm } from "@/hooks/use-confirm";
import {
  useCancelWorkflowInstance,
  useTerminateWorkflowInstance,
  useWorkflowInstanceDetail,
  useWorkflowInstances,
} from "@/graphql/workflows/workflows.hooks";
import type { WorkflowInstance } from "@/graphql/workflows/workflows.types";

import {
  instancesVariables,
  selectInstances,
  WORKFLOW_INSTANCES_LIST,
} from "./workflow-instances-list";

/** The URL param holding the open instance, beside the list's own. */
export const INSTANCE_PARAM = "instance";
export const RUN_PARAM = "instanceRun";

export function useWorkflowInstancesList() {
  const list = useListState(WORKFLOW_INSTANCES_LIST);
  const { state } = list;
  const params = useSearchParams();
  const pathname = usePathname() ?? "";
  const router = useRouter();

  const type = useDebounce(list.filters.type ?? "");
  const { instances, nextCursor, loading, error, refetch } = useWorkflowInstances(
    instancesVariables({ ...list.filters, type }, state.pageSize, state.after)
  );
  const { rows, totalCount } = selectInstances(instances, {
    filters: list.filters,
    q: state.q,
    sort: state.sort,
    page: state.page,
    pageSize: state.pageSize,
  });

  const withInstance = (workflowId: string | null, runId: string | null = null) => {
    const next = new URLSearchParams(params?.toString() ?? "");
    if (workflowId && runId) {
      next.set(INSTANCE_PARAM, workflowId);
      next.set(RUN_PARAM, runId);
    } else {
      next.delete(INSTANCE_PARAM);
      next.delete(RUN_PARAM);
    }
    const qs = next.toString();
    return qs ? `${pathname}?${qs}` : pathname;
  };

  return {
    list,
    rows,
    totalCount,
    nextCursor: nextCursor === state.after ? null : nextCursor,
    loading: loading && instances.length === 0,
    stale: loading && instances.length > 0,
    error: error
      ? { message: "Could not refresh workflow instances. Retry to check the current state." }
      : nextCursor && nextCursor === state.after
        ? { message: "The workflow engine returned a repeated page cursor. Refresh the list." }
        : null,
    onRetry: () => void refetch().catch(() => {}),
    instanceHref: (i: WorkflowInstance) => withInstance(i.workflowId, i.runId),
    selectedWorkflowId: params?.get(INSTANCE_PARAM) || null,
    selectedRunId: params?.get(RUN_PARAM) || null,
    onCloseInstance: () => router.replace(withInstance(null), { scroll: false }),
  };
}

/** One instance's detail and activity feed, for InstanceDetailView. */
export function useWorkflowInstanceDetailPanel(workflowId: string | null, runId: string | null) {
  const { detail, loading, error, refetch } = useWorkflowInstanceDetail(
    runId ? workflowId : null,
    runId
  );
  return {
    workflowId,
    detail,
    loading,
    error:
      workflowId && !runId
        ? {
            message:
              "This link has no execution ID. Close it and select an instance from the list.",
          }
        : error
          ? { message: "Could not load this execution. Check your access and retry." }
          : workflowId && runId && !loading && !detail
            ? { message: "This execution is unavailable or you do not have access to it." }
            : null,
    refetch: () => {
      void refetch().catch(() => {});
    },
  };
}

/**
 * Cancel / terminate for a running instance, for InstanceAdminControlsView.
 * The admin-only check happens on the resolver.
 */
export function useInstanceAdminControls(workflowId: string, runId: string, onAfter: () => void) {
  const confirm = useConfirm();
  const [cancelMutation, { loading: cancelLoading }] = useCancelWorkflowInstance();
  const [terminateMutation, { loading: terminateLoading }] = useTerminateWorkflowInstance();
  const target = useRef({ workflowId, runId, active: true });
  useEffect(() => {
    target.current = { workflowId, runId, active: true };
    return () => {
      target.current.active = false;
    };
  }, [workflowId, runId]);
  const stillSelected = () =>
    target.current.active &&
    target.current.workflowId === workflowId &&
    target.current.runId === runId;

  const onCancel = async () => {
    const ok = await confirm({
      title: "Cancel workflow?",
      description: `Sends a cooperative cancel request to ${workflowId}, execution ${runId}. The workflow may run cleanup before exiting.`,
      confirmLabel: "Cancel workflow",
      cancelLabel: "Keep running",
    });
    if (!ok || !stillSelected()) return;
    try {
      const { data } = await cancelMutation({ variables: { workflowId, runId } });
      if (data?.cancelWorkflowInstance?.ok) {
        toast.success("Cancel signal sent");
        onAfter();
      } else {
        const msg = data?.cancelWorkflowInstance?.errors?.[0]?.messages?.[0] ?? "Cancel failed";
        toast.error(msg);
      }
    } catch {
      toast.error(
        "The request could not be confirmed. Refresh this execution to check its status and your permissions before retrying."
      );
    }
  };

  const onTerminate = async () => {
    const ok = await confirm({
      title: "Terminate workflow?",
      description: `Hard kill of ${workflowId}, execution ${runId}. No cleanup runs. Reserve for wedged workflows the cooperative cancel cannot unstick.`,
      confirmLabel: "Terminate",
      cancelLabel: "Keep running",
    });
    if (!ok || !stillSelected()) return;
    const reason = window.prompt("Termination reason (required):");
    if (!reason || !reason.trim()) {
      toast.error("Termination reason is required");
      return;
    }
    try {
      const { data } = await terminateMutation({
        variables: { workflowId, runId, reason: reason.trim() },
      });
      if (data?.terminateWorkflowInstance?.ok) {
        toast.success("Workflow terminated");
        onAfter();
      } else {
        const msg =
          data?.terminateWorkflowInstance?.errors?.[0]?.messages?.[0] ?? "Terminate failed";
        toast.error(msg);
      }
    } catch {
      toast.error(
        "The request could not be confirmed. Refresh this execution to check its status and your permissions before retrying."
      );
    }
  };

  return { onCancel, onTerminate, cancelLoading, terminateLoading };
}
