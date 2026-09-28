"use client";

import { useState } from "react";
import { gql } from "@apollo/client";
import { useQuery } from "@apollo/client/react";
import { toast } from "sonner";
import { useSearchParams, useRouter, usePathname } from "next/navigation";

import { useCursorTable, type CursorPage } from "@/components/data-table";
import { useConfirm } from "@/hooks/use-confirm";
import {
  useDeleteConfiguredWorkflow,
  useDeleteDefinition,
  useRunWorkflow,
  useRunWorkflowDefinition,
  useUpdateConfiguredWorkflow,
  useWorkflowDefinitionRuns,
  useWorkflowDefinitions,
  useWorkflowsEntitlement,
} from "@/graphql/workflows/tiered.hooks";
import type {
  ConfiguredWorkflowWithRuns,
  WorkflowDefinitionSummary,
} from "@/graphql/workflows/tiered.types";
import { LIST_WORKFLOW_RUNS } from "@/graphql/operations/operations.queries";
import type { AstroliftWorkflowRun } from "@/graphql/operations/operations.types";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

interface WorkflowRunsResp {
  astroliftWorkflowRuns: AstroliftWorkflowRun[];
}

export type WorkflowTab = "workflows" | "running" | "definitions" | "history";
export const WORKFLOW_TABS: readonly WorkflowTab[] = [
  "workflows",
  "running",
  "definitions",
  "history",
];

// The module list needs each workflow's latest run status; the contracts
// list doc (LIST_CONFIGURED_WORKFLOWS) doesn't fetch runs, so this
// surface-local doc extends it. Fields verified against schema.graphql
// (`workflows(orgId)` → ConfiguredWorkflow → runs: [WorkflowRun!]!).
const CONFIGURED_WORKFLOW_FIELDS = gql`
  fragment ConfiguredWorkflowFields on ConfiguredWorkflow {
    guid
    name
    slug
    description
    triggerKind
    scheduleCron
    isEnabled
    inputs
    stageBindings
    organizationGuid
    definitionSlug
    definitionName
    patternKind
    runCount
    createdAt
    runs {
      guid
      currentState
      temporalWorkflowId
      startedAt
      completedAt
      isCompleted
    }
  }
`;

/**
 * Cursor-paginated configured workflows (#1243). `workflows` is deprecated
 * for returning every one the org owns in one response; `workflowsPage`
 * shipped alongside it and had no document at all until this one.
 */
const LIST_WORKFLOWS_PAGE = gql`
  ${CONFIGURED_WORKFLOW_FIELDS}
  query WorkflowsModuleListPage($orgId: ID, $search: String, $limit: Int, $after: String) {
    workflowsPage(orgId: $orgId, search: $search, limit: $limit, after: $after) {
      items {
        ...ConfiguredWorkflowFields
      }
      nextCursor
      totalCount
    }
  }
`;

interface WorkflowsModulePageData {
  workflowsPage: CursorPage<ConfiguredWorkflowWithRuns>;
}

const TERMINAL_DEFINITION_RUN_STATES = new Set([
  "completed",
  "failed",
  "cancelled",
  "terminated",
  "timed_out",
]);

/**
 * Everything behind /workflows that talks to the server or the URL: the
 * tab in `?tab=`, the definitions, the configured-workflow cursor walk, the
 * definition runs, platform run history, and every mutation. The data half
 * of WorkflowsListScreen.
 */
export function useWorkflowsList() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const rawTab = searchParams.get("tab") as WorkflowTab | null;
  const tab: WorkflowTab = rawTab && WORKFLOW_TABS.includes(rawTab) ? rawTab : "workflows";

  function setTab(next: WorkflowTab) {
    const params = new URLSearchParams(searchParams.toString());
    if (next === "workflows") {
      params.delete("tab");
    } else {
      params.set("tab", next);
    }
    router.replace(`${pathname}${params.size ? `?${params}` : ""}`, { scroll: false });
  }

  const { definitions, loading: defsLoading, error: defsError } = useWorkflowDefinitions();
  const [deleteDefinition] = useDeleteDefinition();
  const confirm = useConfirm();

  // ── Tier-2 configured workflows (default tab) ──
  const entitlement = useWorkflowsEntitlement();
  const permissions = useMyPermissions();
  const canViewPlatformRuns = permissions.can("audit_log.read");
  // `workflowsPage` searches the workflow's own name / slug / description
  // and the name / slug of the definition behind it. No sort argument, so
  // no column declares a `sortKey`. Skipped off-tab, as the flat query was.
  const table = useCursorTable<ConfiguredWorkflowWithRuns>({
    query: LIST_WORKFLOWS_PAGE,
    variables: { orgId: null },
    extract: (d) => (d as WorkflowsModulePageData | undefined)?.workflowsPage,
    searchVariable: "search",
    urlKey: "wf",
    skip: tab !== "workflows",
    fetchPolicy: "cache-and-network",
  });
  const wfRefetch = table.refetch;
  const repositoryWorkflows = definitions.filter(
    (definition) =>
      !definition.isGlobal && Boolean(definition.sourceRepo) && Boolean(definition.projectGuid)
  );
  const [runConfigured] = useRunWorkflow();
  const [updateConfigured] = useUpdateConfiguredWorkflow();
  const [deleteConfigured] = useDeleteConfiguredWorkflow();
  const [busySlug, setBusySlug] = useState<string | null>(null);
  const [runDefinition] = useRunWorkflowDefinition();
  const definitionRuns = useWorkflowDefinitionRuns({
    limit: 100,
    pollInterval: tab === "running" ? 5_000 : 0,
    skip: tab !== "running" && tab !== "history",
  });
  const runningDefinitionRuns = definitionRuns.runs.filter(
    (run) => !TERMINAL_DEFINITION_RUN_STATES.has(run.status)
  );
  const historicalDefinitionRuns = definitionRuns.runs.filter((run) =>
    TERMINAL_DEFINITION_RUN_STATES.has(run.status)
  );

  const handleRunConfigured = async (wf: ConfiguredWorkflowWithRuns) => {
    setBusySlug(wf.slug);
    try {
      const { data } = await runConfigured({ variables: { workflowId: wf.guid } });
      if (data?.runWorkflow?.ok) {
        toast.success("Run started", { description: wf.name });
        wfRefetch();
      } else {
        const errors = data?.runWorkflow?.errors ?? [];
        if (errors.length > 0) {
          for (const e of errors) toast.error(`${e.field}: ${e.messages.join(", ")}`);
        } else {
          toast.error("Failed to start run");
        }
      }
    } finally {
      setBusySlug(null);
    }
  };

  const handleRunDefinition = async (workflow: WorkflowDefinitionSummary) => {
    setBusySlug(workflow.slug);
    try {
      const { data } = await runDefinition({
        variables: { workflowSlug: workflow.slug, triggerPayload: null },
      });
      if (data?.runWorkflowDefinition?.ok) {
        toast.success("Run started", { description: workflow.name });
        setTab("running");
      } else {
        const errors = data?.runWorkflowDefinition?.errors ?? [];
        if (errors.length > 0) {
          for (const error of errors) {
            toast.error(`${error.field}: ${error.messages.join(", ")}`);
          }
        } else {
          toast.error("Failed to start run");
        }
      }
    } finally {
      setBusySlug(null);
    }
  };

  const handleToggleConfigured = async (wf: ConfiguredWorkflowWithRuns) => {
    setBusySlug(wf.slug);
    try {
      const next = !wf.isEnabled;
      const { data } = await updateConfigured({
        variables: { slug: wf.slug, isEnabled: next },
      });
      if (data?.updateWorkflow?.ok) {
        toast.success(next ? "Workflow enabled" : "Workflow disabled", { description: wf.name });
        wfRefetch();
      } else {
        const errors = data?.updateWorkflow?.errors ?? [];
        if (errors.length > 0) {
          for (const e of errors) toast.error(`${e.field}: ${e.messages.join(", ")}`);
        } else {
          toast.error("Failed to update workflow");
        }
      }
    } finally {
      setBusySlug(null);
    }
  };

  const handleDeleteConfigured = async (wf: ConfiguredWorkflowWithRuns) => {
    const ok = await confirm({
      title: `Delete "${wf.name}"?`,
      description: "This will delete the configured workflow. Its definition is not affected.",
      confirmLabel: "Delete",
      cancelLabel: "Cancel",
    });
    if (!ok) return;
    setBusySlug(wf.slug);
    try {
      const { data } = await deleteConfigured({ variables: { slug: wf.slug } });
      if (data?.deleteWorkflow?.ok) {
        toast.success("Workflow deleted", { description: `${wf.name} has been removed.` });
        wfRefetch();
      } else {
        const errors = data?.deleteWorkflow?.errors ?? [];
        if (errors.length > 0) {
          for (const e of errors) toast.error(`${e.field}: ${e.messages.join(", ")}`);
        } else {
          toast.error("Failed to delete workflow");
        }
      }
    } finally {
      setBusySlug(null);
    }
  };

  const { data: runsData, loading: runsLoading } = useQuery<WorkflowRunsResp>(LIST_WORKFLOW_RUNS, {
    variables: { limit: 50 },
    skip: tab !== "history" || permissions.loading || !canViewPlatformRuns,
    fetchPolicy: "cache-and-network",
  });
  const historyRuns = runsData?.astroliftWorkflowRuns ?? [];

  const handleDeleteDefinition = async (wf: WorkflowDefinitionSummary) => {
    const ok = await confirm({
      title: `Delete "${wf.name}"?`,
      description: "This will permanently delete the workflow definition and cannot be undone.",
      confirmLabel: "Delete",
      cancelLabel: "Cancel",
    });
    if (!ok) return;
    const { data } = await deleteDefinition({ variables: { slug: wf.slug } });
    if (data?.deleteWorkflowDefinition?.ok) {
      toast.success("Workflow deleted", { description: `${wf.name} has been removed.` });
    } else {
      for (const e of data?.deleteWorkflowDefinition?.errors ?? []) {
        toast.error(`${e.field}: ${e.messages.join(", ")}`);
      }
    }
  };

  return {
    tab,
    setTab,
    entitlement: {
      canCreate: entitlement.canCreate,
      canManage: entitlement.canManage,
      canRun: entitlement.canRun,
    },
    canViewPlatformRuns,
    definitions,
    defsLoading,
    defsError,
    repositoryWorkflows,
    table,
    busySlug,
    definitionRuns: {
      loading: definitionRuns.loading,
      error: definitionRuns.error,
      running: runningDefinitionRuns,
      historical: historicalDefinitionRuns,
    },
    historyRuns,
    runsLoading,
    onRunConfigured: handleRunConfigured,
    onToggleConfigured: handleToggleConfigured,
    onDeleteConfigured: handleDeleteConfigured,
    onRunDefinition: handleRunDefinition,
    onDeleteDefinition: handleDeleteDefinition,
  };
}
