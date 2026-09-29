"use client";

import { useMemo, useState } from "react";
import { gql } from "@apollo/client";
import { useQuery } from "@apollo/client/react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";

import { useListState } from "@/components/list/use-list-state";
import { useConfirm } from "@/hooks/use-confirm";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import {
  useCloneDefinition,
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
  TieredValidationError,
} from "@/graphql/workflows/tiered.types";
import type { CursorPage } from "@/components/data-table";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

import { joinWorkflows, selectWorkflows, WORKFLOWS_LIST, type WorkflowRow } from "./workflows-list";

// The module list's configured-workflow fields; the contracts list doc
// (LIST_CONFIGURED_WORKFLOWS) doesn't fetch runs, so this surface-local doc
// extends it. Fields verified against schema.graphql
// (`workflowsPage` → ConfiguredWorkflow → runs: [WorkflowRun!]!).
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
 * shipped alongside it.
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

/** The backend's page cap (MAX_PAGE_LIMIT): the list reads this many configured workflows. */
export const CONFIGURED_LIMIT = 200;
/** The newest definition runs read for a definition's last run. */
export const DEFINITION_RUNS_LIMIT = 100;

function toastErrors(errors: TieredValidationError[], fallback: string) {
  if (errors.length === 0) toast.error(fallback);
  for (const e of errors) toast.error(`${e.field}: ${e.messages.join(", ")}`);
}

/**
 * The Workflows list: URL list state, the org's configured workflows (one
 * page at the backend's cap), every visible definition, and the newest
 * definition runs, joined and answered in the browser (see workflows-list.ts).
 * The definition runs are skipped on Templates, whose rows are the
 * platform's and carry no runs of this org's. Every row action is the
 * mutation the old tabs used, behind the same workflows-module grants. The
 * data half of WorkflowsListScreen.
 */
export function useWorkflowsList() {
  const list = useListState(WORKFLOWS_LIST);
  const { state } = list;
  const router = useRouter();
  const confirm = useConfirm();
  const { org } = useActiveOrg();
  const entitlement = useWorkflowsEntitlement();
  const permissions = useMyPermissions();
  const templates = state.view === "templates";

  const configuredQ = useQuery<WorkflowsModulePageData>(LIST_WORKFLOWS_PAGE, {
    variables: { orgId: null, search: null, limit: CONFIGURED_LIMIT, after: null },
    skip: templates,
    fetchPolicy: "cache-and-network",
  });
  const definitionsQ = useWorkflowDefinitions();
  const runsQ = useWorkflowDefinitionRuns({ limit: DEFINITION_RUNS_LIMIT, skip: templates });

  const configured = configuredQ.data?.workflowsPage.items;
  const all: WorkflowRow[] = useMemo(
    () =>
      joinWorkflows(configured ?? [], definitionsQ.definitions, runsQ.runs, {
        runsComplete: runsQ.runs.length < DEFINITION_RUNS_LIMIT,
      }),
    [configured, definitionsQ.definitions, runsQ.runs]
  );
  const { rows, totalCount } = selectWorkflows(all, {
    q: state.q,
    filters: list.filters,
    sort: state.sort,
    page: state.page,
    pageSize: state.pageSize,
  });

  const [runConfigured] = useRunWorkflow();
  const [runDefinition] = useRunWorkflowDefinition();
  const [updateConfigured] = useUpdateConfiguredWorkflow();
  const [deleteConfigured] = useDeleteConfiguredWorkflow();
  const [deleteDefinition] = useDeleteDefinition();
  const [cloneDefinition] = useCloneDefinition();
  const [busySlug, setBusySlug] = useState<string | null>(null);

  const refetchConfigured = () => void configuredQ.refetch();

  async function busy(slug: string, work: () => Promise<void>) {
    setBusySlug(slug);
    try {
      await work();
    } finally {
      setBusySlug(null);
    }
  }

  const onRun = (row: WorkflowRow) =>
    busy(row.slug, async () => {
      if (row.configured) {
        const { data } = await runConfigured({ variables: { workflowId: row.configured.guid } });
        if (!data?.runWorkflow?.ok)
          return toastErrors(data?.runWorkflow?.errors ?? [], "Failed to start run");
      } else {
        const { data } = await runDefinition({
          variables: { workflowSlug: row.slug, triggerPayload: null },
        });
        if (!data?.runWorkflowDefinition?.ok)
          return toastErrors(data?.runWorkflowDefinition?.errors ?? [], "Failed to start run");
      }
      toast.success("Run started", { description: row.name });
      router.push(`/workflows/${encodeURIComponent(row.slug)}/runs`);
    });

  const onToggle = (row: WorkflowRow) =>
    busy(row.slug, async () => {
      const next = !row.isEnabled;
      const { data } = await updateConfigured({ variables: { slug: row.slug, isEnabled: next } });
      if (!data?.updateWorkflow?.ok)
        return toastErrors(data?.updateWorkflow?.errors ?? [], "Failed to update workflow");
      toast.success(next ? "Workflow enabled" : "Workflow disabled", { description: row.name });
      refetchConfigured();
    });

  const onDelete = async (row: WorkflowRow) => {
    const ok = await confirm({
      title: `Delete "${row.name}"?`,
      description: row.configured
        ? "This will delete the configured workflow. Its definition is not affected."
        : "This will permanently delete the workflow definition and cannot be undone.",
      confirmLabel: "Delete",
      cancelLabel: "Cancel",
    });
    if (!ok) return;
    await busy(row.slug, async () => {
      if (row.configured) {
        const { data } = await deleteConfigured({ variables: { slug: row.slug } });
        if (!data?.deleteWorkflow?.ok)
          return toastErrors(data?.deleteWorkflow?.errors ?? [], "Failed to delete workflow");
        refetchConfigured();
      } else {
        const { data } = await deleteDefinition({ variables: { slug: row.slug } });
        if (!data?.deleteWorkflowDefinition?.ok)
          return toastErrors(
            data?.deleteWorkflowDefinition?.errors ?? [],
            "Failed to delete workflow"
          );
      }
      toast.success("Workflow deleted", { description: `${row.name} has been removed.` });
    });
  };

  const onClone = (row: WorkflowRow) =>
    busy(row.slug, async () => {
      const { data } = await cloneDefinition({
        variables: { slug: row.slug, orgId: org?.id ?? null },
      });
      const result = data?.cloneWorkflowDefinition;
      if (!result?.ok || !result.slug)
        return toastErrors(result?.errors ?? [], "Failed to clone template");
      toast.success(`Cloned "${row.name}"`, {
        description: "Your editable copy is ready in the builder.",
      });
      router.push(`/workflows/${encodeURIComponent(result.slug)}`);
    });

  const configuredWaiting = !templates && configuredQ.loading && !configuredQ.data;
  const definitionsWaiting = definitionsQ.loading && definitionsQ.definitions.length === 0;
  const failed = (!templates && configuredQ.error) || definitionsQ.error;
  const totalConfigured = configuredQ.data?.workflowsPage.totalCount ?? null;

  return {
    list,
    rows,
    totalCount,
    loading: configuredWaiting || definitionsWaiting,
    stale:
      (configuredQ.loading && Boolean(configuredQ.data)) ||
      (definitionsQ.loading && definitionsQ.definitions.length > 0),
    error: failed && all.length === 0 ? { message: failed.message } : null,
    onRetry: () => {
      refetchConfigured();
      void definitionsQ.refetch();
    },
    /** Set when the org has more configured workflows than one page holds. */
    truncatedAt:
      totalConfigured !== null && totalConfigured > CONFIGURED_LIMIT ? CONFIGURED_LIMIT : null,
    canCreate: entitlement.canCreate,
    canManage: entitlement.canManage,
    canRun: entitlement.canRun,
    // Temporal instances are platform operations: behind the audit read, as before.
    canViewPlatformRuns: permissions.can("audit_log.read"),
    busySlug,
    onRun,
    onToggle,
    onDelete,
    onClone,
  };
}
