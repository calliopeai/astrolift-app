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
  UPDATE_CONFIGURED_WORKFLOW,
  UPDATE_WORKFLOW_DEFINITION_TIERED,
  UPDATE_WORKFLOW_STAGE,
} from "./tiered.mutations";
import {
  EXPORT_WORKFLOW_MANIFEST,
  GET_CONFIGURED_WORKFLOW,
  GET_TIERED_WORKFLOW_DEFINITION,
  LIST_CONFIGURED_WORKFLOWS,
  LIST_TIERED_WORKFLOW_DEFINITIONS,
  LIST_WORKFLOW_RUNS,
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
  TieredWorkflowData,
  TieredWorkflowDefinitionData,
  TieredWorkflowDefinitionsData,
  TieredWorkflowRunsData,
  TieredWorkflowsData,
  TieredWorkflowStageExecutionsData,
  TieredWorkflowStagesData,
  UpdateConfiguredWorkflowData,
  UpdateWorkflowDefinitionTieredData,
  UpdateWorkflowStageData,
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

export const useWorkflowDefinitions = (orgId?: string | null) => {
  const { data, loading, error, refetch } = useQuery<TieredWorkflowDefinitionsData>(
    LIST_TIERED_WORKFLOW_DEFINITIONS,
    {
      variables: { orgId: orgId ?? null },
      fetchPolicy: "cache-and-network",
    }
  );
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

export const useWorkflowRuns = (workflowId: string | null, orgId?: string | null) => {
  const { data, loading, error, refetch } = useQuery<TieredWorkflowRunsData>(LIST_WORKFLOW_RUNS, {
    variables: { workflowId: workflowId ?? "", orgId: orgId ?? null },
    fetchPolicy: "cache-and-network",
    skip: !workflowId,
  });
  return { runs: data?.workflowRuns ?? [], loading, error, refetch };
};

export const useWorkflowStageExecutions = (params: {
  workflowId: string | null;
  runId: string | null;
}) => {
  const { data, loading, error, refetch } = useQuery<TieredWorkflowStageExecutionsData>(
    LIST_WORKFLOW_STAGE_EXECUTIONS,
    {
      variables: { workflowId: params.workflowId ?? "", runId: params.runId ?? "" },
      fetchPolicy: "cache-and-network",
      skip: !params.workflowId || !params.runId,
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
