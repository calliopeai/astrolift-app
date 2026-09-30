"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { toast } from "sonner";

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

/**
 * The Platform instances list's data half (#437, spec 44 §5.1): list state
 * in the URL, Type and Status sent to `astroliftWorkflowInstances`, one page
 * at the backend's cap, then search, sort and numbered pages in the browser
 * (see workflow-instances-list.ts). The open instance is `?instance=`, so a
 * row is a link and the detail survives a reload.
 */
export function useWorkflowInstancesList() {
  const list = useListState(WORKFLOW_INSTANCES_LIST);
  const { state } = list;
  const params = useSearchParams();
  const pathname = usePathname() ?? "";
  const router = useRouter();

  const type = useDebounce(list.filters.type ?? "");
  const { instances, loading, error, refetch } = useWorkflowInstances(
    instancesVariables({ ...list.filters, type })
  );
  const { rows, totalCount } = selectInstances(instances, {
    filters: list.filters,
    q: state.q,
    sort: state.sort,
    page: state.page,
    pageSize: state.pageSize,
  });

  const withInstance = (workflowId: string | null) => {
    const next = new URLSearchParams(params?.toString() ?? "");
    if (workflowId) next.set(INSTANCE_PARAM, workflowId);
    else next.delete(INSTANCE_PARAM);
    const qs = next.toString();
    return qs ? `${pathname}?${qs}` : pathname;
  };

  return {
    list,
    rows,
    totalCount,
    loading: loading && instances.length === 0,
    stale: loading && instances.length > 0,
    error: error && instances.length === 0 ? { message: error.message } : null,
    onRetry: () => void refetch(),
    instanceHref: (i: WorkflowInstance) => withInstance(i.workflowId),
    selectedWorkflowId: params?.get(INSTANCE_PARAM) || null,
    onCloseInstance: () => router.replace(withInstance(null), { scroll: false }),
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
