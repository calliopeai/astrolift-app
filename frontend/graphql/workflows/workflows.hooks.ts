import { useMutation, useQuery } from "@apollo/client/react";

import { GET_WORKFLOW_INSTANCE_DETAIL, GET_WORKFLOW_INSTANCES } from "./workflows.queries";
import {
  CANCEL_WORKFLOW_INSTANCE,
  SIGNAL_WORKFLOW_INSTANCE,
  START_WORKFLOW,
  TERMINATE_WORKFLOW_INSTANCE,
  TRANSITION_WORKFLOW,
} from "./workflows.mutations";
import type {
  CancelWorkflowInstanceData,
  SignalWorkflowInstanceData,
  StartWorkflowData,
  TerminateWorkflowInstanceData,
  TransitionWorkflowData,
  WorkflowInstanceDetailData,
  WorkflowInstancesData,
} from "./workflows.types";

export const useStartWorkflow = () => useMutation<StartWorkflowData>(START_WORKFLOW);

export const useTransitionWorkflow = () => useMutation<TransitionWorkflowData>(TRANSITION_WORKFLOW);

// ─── Temporal instance viewer (#437) ────────────────────────────────────

export const useWorkflowInstances = (params: {
  workflowType?: string | null;
  status?: string | null;
  limit?: number;
}) => {
  const { data, loading, error, refetch } = useQuery<WorkflowInstancesData>(
    GET_WORKFLOW_INSTANCES,
    {
      variables: {
        workflowType: params.workflowType ?? null,
        status: params.status ?? null,
        limit: params.limit ?? 50,
      },
      fetchPolicy: "cache-and-network",
    }
  );
  return {
    instances: data?.astroliftWorkflowInstances?.items ?? [],
    nextCursor: data?.astroliftWorkflowInstances?.nextCursor ?? null,
    loading,
    error,
    refetch,
  };
};

export const useWorkflowInstanceDetail = (workflowId: string | null) => {
  const { data, loading, error, refetch } = useQuery<WorkflowInstanceDetailData>(
    GET_WORKFLOW_INSTANCE_DETAIL,
    {
      variables: { workflowId: workflowId ?? "" },
      fetchPolicy: "cache-and-network",
      skip: !workflowId,
    }
  );
  return {
    detail: data?.astroliftWorkflowInstanceDetail ?? null,
    loading,
    error,
    refetch,
  };
};

export const useCancelWorkflowInstance = () =>
  useMutation<CancelWorkflowInstanceData>(CANCEL_WORKFLOW_INSTANCE);

export const useTerminateWorkflowInstance = () =>
  useMutation<TerminateWorkflowInstanceData>(TERMINATE_WORKFLOW_INSTANCE);

export const useSignalWorkflowInstance = () =>
  useMutation<SignalWorkflowInstanceData>(SIGNAL_WORKFLOW_INSTANCE);
