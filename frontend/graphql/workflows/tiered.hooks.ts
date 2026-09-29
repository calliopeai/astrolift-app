"use client";

import { useLazyQuery, useMutation, useQuery } from "@apollo/client/react";

import { useModules } from "@/graphql/user/user.hooks";

import {
  CLONE_WORKFLOW_DEFINITION,
  CREATE_CONFIGURED_WORKFLOW,
  CREATE_WORKFLOW_STAGE,
  DELETE_CONFIGURED_WORKFLOW,
  DELETE_WORKFLOW_DEFINITION_TIERED,
  DELETE_WORKFLOW_STAGE,
  IMPORT_WORKFLOW_MANIFEST,
  REORDER_WORKFLOW_STAGES,
  RUN_WORKFLOW,
  RUN_WORKFLOW_DEFINITION,
  UPDATE_CONFIGURED_WORKFLOW,
  UPDATE_WORKFLOW_DEFINITION_TIERED,
  UPDATE_WORKFLOW_STAGE,
} from "./tiered.mutations";
import {
  EXPORT_WORKFLOW_MANIFEST,
  GET_CONFIGURED_WORKFLOW,
  GET_TIERED_WORKFLOW_DEFINITION,
  GET_WORKFLOW_DEFINITION_RUN,
  LIST_CONFIGURED_WORKFLOWS,
  LIST_TIERED_WORKFLOW_DEFINITIONS,
  LIST_WORKFLOW_RUNS,
  LIST_WORKFLOW_DEFINITION_RUNS,
  LIST_PENDING_HUMAN_GATES,
  LIST_WORKFLOW_STAGE_EXECUTIONS,
  LIST_WORKFLOW_STAGES,
  PREVIEW_WORKFLOW_MANIFEST,
} from "./tiered.queries";
import type {
  CloneWorkflowDefinitionData,
  CreateConfiguredWorkflowData,
  CreateWorkflowStageData,
  DeleteConfiguredWorkflowData,
  DeleteWorkflowDefinitionTieredData,
  DeleteWorkflowStageData,
  ExportWorkflowManifestData,
  ImportWorkflowManifestData,
  PreviewWorkflowManifestData,
  ReorderWorkflowStagesData,
  RunWorkflowData,
  RunWorkflowDefinitionData,
  TieredWorkflowData,
  TieredWorkflowDefinitionData,
  TieredWorkflowDefinitionsData,
  TieredWorkflowDefinitionsVars,
  TieredWorkflowRunsData,
  WorkflowDefinitionRunsData,
  WorkflowDefinitionRunsVars,
  PendingHumanGatesData,
  PendingHumanGatesVars,
  TieredWorkflowsData,
  TieredWorkflowStageExecutionsData,
  TieredWorkflowStagesData,
  UpdateConfiguredWorkflowData,
  UpdateWorkflowDefinitionTieredData,
  UpdateWorkflowStageData,
  WorkflowDefinitionRun,
} from "./tiered.types";

// ─── Entitlement ─────────────────────────────────────────────────────────

/**
 * Server-authoritative capability flags for the workflows module
 * (`me.modules` key `"workflows"`). Thin per-module view over the shared
 * {@link useModules} manifest (SSR-primed GET_ME cache) — affordances
 * render exactly what the server answered, no client permission math.
 */
export const useWorkflowsEntitlement = () => {
  const { canView, canCreate, canManage, canRun, loading, error } = useModules();
  return {
    canView: canView("workflows"),
    canCreate: canCreate("workflows"),
    canManage: canManage("workflows"),
    canRun: canRun("workflows"),
    loading,
    error,
  };
};

// ─── Tier 2 — configured workflows ───────────────────────────────────────

export const useTieredWorkflows = (orgId?: string | null) => {
  const { data, loading, error, refetch } = useQuery<TieredWorkflowsData>(
    LIST_CONFIGURED_WORKFLOWS,
    {
      variables: { orgId: orgId ?? null },
      fetchPolicy: "cache-and-network",
    }
  );
  return { workflows: data?.workflows ?? [], loading, error, refetch };
};

export const useTieredWorkflow = (slug: string | null, orgId?: string | null) => {
  const { data, loading, error, refetch } = useQuery<TieredWorkflowData>(GET_CONFIGURED_WORKFLOW, {
    variables: { slug: slug ?? "", orgId: orgId ?? null },
    fetchPolicy: "cache-and-network",
    skip: !slug,
  });
  return { workflow: data?.workflow ?? null, loading, error, refetch };
};

export const useCreateConfiguredWorkflow = () =>
  useMutation<CreateConfiguredWorkflowData>(CREATE_CONFIGURED_WORKFLOW, {
    refetchQueries: [LIST_CONFIGURED_WORKFLOWS],
  });

export const useUpdateConfiguredWorkflow = () =>
  useMutation<UpdateConfiguredWorkflowData>(UPDATE_CONFIGURED_WORKFLOW, {
    refetchQueries: [LIST_CONFIGURED_WORKFLOWS],
  });

export const useDeleteConfiguredWorkflow = () =>
  useMutation<DeleteConfiguredWorkflowData>(DELETE_CONFIGURED_WORKFLOW, {
    refetchQueries: [LIST_CONFIGURED_WORKFLOWS],
  });

export const useRunWorkflow = () =>
  useMutation<RunWorkflowData>(RUN_WORKFLOW, {
    refetchQueries: [LIST_CONFIGURED_WORKFLOWS, LIST_WORKFLOW_RUNS],
  });

// ─── Tier 1 — definitions ────────────────────────────────────────────────

export const useWorkflowDefinitions = (orgId?: string | null, projectId?: string | null) => {
  const { data, loading, error, refetch } = useQuery<
    TieredWorkflowDefinitionsData,
    TieredWorkflowDefinitionsVars
  >(LIST_TIERED_WORKFLOW_DEFINITIONS, {
    variables: { orgId: orgId ?? null, projectId: projectId ?? null },
    fetchPolicy: "cache-and-network",
  });
  return { definitions: data?.workflowDefinitions ?? [], loading, error, refetch };
};

export const useWorkflowDefinition = (slug: string | null, orgId?: string | null) => {
  const { data, loading, error, refetch } = useQuery<TieredWorkflowDefinitionData>(
    GET_TIERED_WORKFLOW_DEFINITION,
    {
      variables: { slug: slug ?? "", orgId: orgId ?? null },
      fetchPolicy: "cache-and-network",
      skip: !slug,
    }
  );
  return { definition: data?.workflowDefinition ?? null, loading, error, refetch };
};

export const useCloneDefinition = () =>
  useMutation<CloneWorkflowDefinitionData>(CLONE_WORKFLOW_DEFINITION, {
    refetchQueries: [LIST_TIERED_WORKFLOW_DEFINITIONS],
  });

export const useUpdateDefinition = () =>
  useMutation<UpdateWorkflowDefinitionTieredData>(UPDATE_WORKFLOW_DEFINITION_TIERED, {
    refetchQueries: [LIST_TIERED_WORKFLOW_DEFINITIONS, GET_TIERED_WORKFLOW_DEFINITION],
  });

export const useDeleteDefinition = () =>
  useMutation<DeleteWorkflowDefinitionTieredData>(DELETE_WORKFLOW_DEFINITION_TIERED, {
    refetchQueries: [LIST_TIERED_WORKFLOW_DEFINITIONS],
  });

export const useRunWorkflowDefinition = () =>
  useMutation<RunWorkflowDefinitionData>(RUN_WORKFLOW_DEFINITION, {
    refetchQueries: [LIST_WORKFLOW_DEFINITION_RUNS],
  });

// ─── Stages ──────────────────────────────────────────────────────────────

export const useWorkflowStages = (workflowSlug: string | null) => {
  const { data, loading, error, refetch } = useQuery<TieredWorkflowStagesData>(
    LIST_WORKFLOW_STAGES,
    {
      variables: { workflowSlug: workflowSlug ?? "" },
      fetchPolicy: "cache-and-network",
      skip: !workflowSlug,
    }
  );
  return { stages: data?.workflowStages ?? [], loading, error, refetch };
};

export const useCreateWorkflowStage = () =>
  useMutation<CreateWorkflowStageData>(CREATE_WORKFLOW_STAGE, {
    refetchQueries: [LIST_WORKFLOW_STAGES, LIST_TIERED_WORKFLOW_DEFINITIONS],
  });

export const useUpdateWorkflowStage = () =>
  useMutation<UpdateWorkflowStageData>(UPDATE_WORKFLOW_STAGE, {
    refetchQueries: [LIST_WORKFLOW_STAGES],
  });

export const useDeleteWorkflowStage = () =>
  useMutation<DeleteWorkflowStageData>(DELETE_WORKFLOW_STAGE, {
    refetchQueries: [LIST_WORKFLOW_STAGES, LIST_TIERED_WORKFLOW_DEFINITIONS],
  });

export const useReorderWorkflowStages = () =>
  useMutation<ReorderWorkflowStagesData>(REORDER_WORKFLOW_STAGES, {
    refetchQueries: [LIST_WORKFLOW_STAGES],
  });

// ─── Tier 3 — runs ───────────────────────────────────────────────────────

// Exposes startPolling/stopPolling so a caller can gate polling on the query's
// own output — the live workflow DAG (#1090) polls while its focus run is
// non-terminal and stops once it settles.
export const useWorkflowRuns = (workflowId: string | null, orgId?: string | null) => {
  const { data, loading, error, refetch, startPolling, stopPolling } =
    useQuery<TieredWorkflowRunsData>(LIST_WORKFLOW_RUNS, {
      variables: { workflowId: workflowId ?? "", orgId: orgId ?? null },
      fetchPolicy: "cache-and-network",
      skip: !workflowId,
    });
  return { runs: data?.workflowRuns ?? [], loading, error, refetch, startPolling, stopPolling };
};

export const useWorkflowDefinitionRuns = (params?: {
  orgId?: string | null;
  projectId?: string | null;
  status?: string | null;
  limit?: number;
  pollInterval?: number;
  skip?: boolean;
}) => {
  const { data, loading, error, refetch, startPolling, stopPolling } = useQuery<
    WorkflowDefinitionRunsData,
    WorkflowDefinitionRunsVars
  >(LIST_WORKFLOW_DEFINITION_RUNS, {
    variables: {
      orgId: params?.orgId ?? null,
      projectId: params?.projectId ?? null,
      status: params?.status ?? null,
      limit: params?.limit ?? 50,
    },
    fetchPolicy: "cache-and-network",
    pollInterval: params?.pollInterval ?? 0,
    skip: params?.skip ?? false,
  });
  return {
    runs: data?.workflowDefinitionRuns ?? [],
    loading,
    error,
    refetch,
    startPolling,
    stopPolling,
  };
};

// One definition run by guid (#2155): the run page's own read, so a run
// past the newest window of the definition's runs still resolves.
export const useWorkflowDefinitionRun = (params: {
  guid: string | null;
  orgId?: string | null;
  skip?: boolean;
}) => {
  const { data, loading, error, refetch, startPolling, stopPolling } = useQuery<{
    workflowDefinitionRun: WorkflowDefinitionRun | null;
  }>(GET_WORKFLOW_DEFINITION_RUN, {
    variables: { guid: params.guid ?? "", orgId: params.orgId ?? null },
    fetchPolicy: "cache-and-network",
    skip: !params.guid || (params.skip ?? false),
  });
  return {
    run: data?.workflowDefinitionRun ?? null,
    loading,
    error,
    refetch,
    startPolling,
    stopPolling,
  };
};

// Org-wide pending-gates list (#1820): every open human_gate the caller may
// decide, across every run, the "an owner does not have to open every
// workflow to find them" view. Same query, same authorization, as the CLI's
// `astro workflow gates` / `astro workflow gate`.
export const usePendingHumanGates = (params?: {
  orgId?: string | null;
  limit?: number;
  pollInterval?: number;
  skip?: boolean;
}) => {
  const { data, loading, error, refetch } = useQuery<PendingHumanGatesData, PendingHumanGatesVars>(
    LIST_PENDING_HUMAN_GATES,
    {
      variables: { orgId: params?.orgId ?? null, limit: params?.limit ?? 50 },
      fetchPolicy: "cache-and-network",
      pollInterval: params?.pollInterval ?? 0,
      skip: params?.skip ?? false,
    }
  );
  return { gates: data?.pendingHumanGates ?? [], loading, error, refetch };
};

// `pollInterval` is passed straight to Apollo, which treats it reactively: a
// non-zero value polls at that cadence, switching back to 0 stops. The live
// DAG (#1090) drives it from run terminality (0 once the run settles).
export const useWorkflowStageExecutions = (params: {
  workflowId: string | null;
  runId: string | null;
  pollInterval?: number;
}) => {
  const { data, loading, error, refetch } = useQuery<TieredWorkflowStageExecutionsData>(
    LIST_WORKFLOW_STAGE_EXECUTIONS,
    {
      variables: { workflowId: params.workflowId ?? "", runId: params.runId ?? "" },
      fetchPolicy: "cache-and-network",
      skip: !params.workflowId || !params.runId,
      pollInterval: params.pollInterval ?? 0,
    }
  );
  return { executions: data?.workflowStageExecutions ?? [], loading, error, refetch };
};

// ─── Manifest (TOML code view) ───────────────────────────────────────────

// User-triggered fetches — lazy so the code view only round-trips on demand.
export const useManifestExport = () =>
  useLazyQuery<ExportWorkflowManifestData>(EXPORT_WORKFLOW_MANIFEST, {
    fetchPolicy: "network-only",
  });

export const useManifestPreview = () =>
  useLazyQuery<PreviewWorkflowManifestData>(PREVIEW_WORKFLOW_MANIFEST, {
    fetchPolicy: "network-only",
  });

export const useManifestImport = () =>
  useMutation<ImportWorkflowManifestData>(IMPORT_WORKFLOW_MANIFEST, {
    refetchQueries: [LIST_TIERED_WORKFLOW_DEFINITIONS],
  });
