"use client";

import { useState } from "react";
import Link from "next/link";
import {
  ClockIcon,
  Loader2Icon,
  PlayIcon,
  PlusIcon,
  PowerIcon,
  PowerOffIcon,
  TrashIcon,
  WorkflowIcon,
  WrenchIcon,
} from "lucide-react";
import { gql } from "@apollo/client";
import { useQuery } from "@apollo/client/react";
import { toast } from "sonner";
import { useSearchParams, useRouter, usePathname } from "next/navigation";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
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
  WorkflowDefinitionRun,
  WorkflowDefinitionSummary,
} from "@/graphql/workflows/tiered.types";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { formatTriggerKind } from "./[slug]/components/workflow-detail-shell";
import { latestRun, RunStateBadge } from "./[slug]/components/run-content";
import { LIST_WORKFLOW_RUNS } from "@/graphql/operations/operations.queries";
import type { AstroliftWorkflowRun } from "@/graphql/operations/operations.types";
import { ListControls } from "@/components/ListControls";
import { WorkflowTopology } from "@/components/workflows/workflow-topology";
import { useListControls } from "@/hooks/use-list-controls";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

interface WorkflowRunsResp {
  astroliftWorkflowRuns: AstroliftWorkflowRun[];
}

import { WorkflowInstancesPanel } from "./instances-panel";

type WorkflowTab = "workflows" | "running" | "definitions" | "history";
const TABS: readonly WorkflowTab[] = ["workflows", "running", "definitions", "history"];

const TAB_LABELS: Record<WorkflowTab, string> = {
  workflows: "Workflows",
  running: "Running",
  definitions: "Templates",
  history: "History",
};

// The module list needs each workflow's latest run status; the contracts
// list doc (LIST_CONFIGURED_WORKFLOWS) doesn't fetch runs, so this
// surface-local doc extends it. Fields verified against schema.graphql
// (`workflows(orgId)` → ConfiguredWorkflow → runs: [WorkflowRun!]!).
const LIST_WORKFLOWS_WITH_RUNS = gql`
  query WorkflowsModuleList($orgId: ID) {
    workflows(orgId: $orgId) {
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
  }
`;

interface WorkflowsModuleListData {
  workflows: ConfiguredWorkflowWithRuns[];
}

const TERMINAL_DEFINITION_RUN_STATES = new Set([
  "completed",
  "failed",
  "cancelled",
  "terminated",
  "timed_out",
]);

function DefinitionRunList({ runs }: { runs: WorkflowDefinitionRun[] }) {
  return (
    <div className="flex flex-col gap-2">
      {runs.map((run) => (
        <div
          key={run.guid}
          className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-md border px-4 py-3 text-sm"
        >
          <Badge
            variant={
              run.status === "running"
                ? "default"
                : run.status === "completed"
                  ? "secondary"
                  : run.status === "cancelled"
                    ? "outline"
                    : "destructive"
            }
            className="capitalize"
          >
            {run.status.replace(/_/g, " ")}
          </Badge>
          <Link
            href={`/workflows/${encodeURIComponent(run.definitionSlug)}/builder`}
            className="font-medium hover:underline"
          >
            {run.definitionName}
          </Link>
          {run.projectSlug && (
            <Link
              href={`/projects/${encodeURIComponent(run.projectSlug)}`}
              className="text-muted-foreground hover:text-foreground hover:underline"
            >
              {run.projectSlug}
            </Link>
          )}
          {run.currentStageRole && (
            <span className="text-muted-foreground">
              Stage {run.currentStageOrder == null ? "" : `${run.currentStageOrder + 1}: `}
              {run.currentStageRole}
            </span>
          )}
          <span
            className="text-muted-foreground ml-auto max-w-full truncate font-mono text-xs"
            title={run.temporalWorkflowId}
          >
            {run.temporalWorkflowId}
          </span>
          <span className="text-muted-foreground shrink-0 text-xs">
            {run.startedAt ? new Date(run.startedAt).toLocaleString() : "Not started"}
          </span>
        </div>
      ))}
    </div>
  );
}

export default function WorkflowsPage() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const rawTab = searchParams.get("tab") as WorkflowTab | null;
  const tab: WorkflowTab = rawTab && TABS.includes(rawTab) ? rawTab : "workflows";

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
  const {
    data: wfData,
    loading: wfLoading,
    error: wfError,
    refetch: wfRefetch,
  } = useQuery<WorkflowsModuleListData>(LIST_WORKFLOWS_WITH_RUNS, {
    variables: { orgId: null },
    skip: tab !== "workflows",
    fetchPolicy: "cache-and-network",
  });
  const configuredWorkflows = wfData?.workflows ?? [];
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

  // No sort: the definitions grid renders cards, not table rows, and a
  // sort control detached from a column header is a control with nothing
  // to point at.
  const defsCtrl = useListControls<WorkflowDefinitionSummary>({
    data: definitions,
    searchFn: (wf) => [wf.name, wf.description ?? "", wf.patternKind ?? ""].join(" "),
    initialPageSize: 25,
  });

  const { data: runsData, loading: runsLoading } = useQuery<WorkflowRunsResp>(LIST_WORKFLOW_RUNS, {
    variables: { limit: 50 },
    skip: tab !== "history" || permissions.loading || !canViewPlatformRuns,
    fetchPolicy: "cache-and-network",
  });
  const historyRuns = runsData?.astroliftWorkflowRuns ?? [];

  const handleDelete = async (wf: WorkflowDefinitionSummary) => {
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

  return (
    <PageShell
      title="Workflows"
      description="Runnable repository pipelines, configured automations, and execution state."
      actions={
        (tab === "workflows" || tab === "definitions") && entitlement.canCreate ? (
          <Button asChild>
            <Link href="/workflows/new">
              <PlusIcon className="mr-1 h-4 w-4" /> New Workflow
            </Link>
          </Button>
        ) : null
      }
    >
      {/* Tab bar */}
      <div className="mb-4 flex gap-1 border-b pb-0">
        {TABS.map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={[
              "-mb-px border-b-2 px-4 py-2 text-sm font-medium transition-colors",
              t === tab
                ? "border-primary text-foreground"
                : "text-muted-foreground hover:text-foreground border-transparent",
            ].join(" ")}
          >
            {TAB_LABELS[t]}
          </button>
        ))}
      </div>

      {/* Workflows — repository pipelines plus optional tier-2 configured wrappers */}
      {tab === "workflows" && (
        <div className="flex flex-col gap-4">
          {(wfLoading || defsLoading) &&
            configuredWorkflows.length === 0 &&
            repositoryWorkflows.length === 0 && (
              <div className="flex items-center justify-center p-12">
                <Loader2Icon className="text-muted-foreground h-6 w-6 animate-spin" />
              </div>
            )}
          {(wfError || defsError) && (
            <div className="text-destructive bg-destructive/10 border-destructive/20 rounded-md border p-4 text-sm">
              Error: {(wfError ?? defsError)?.message}
            </div>
          )}
          {!wfLoading &&
            !defsLoading &&
            !wfError &&
            !defsError &&
            configuredWorkflows.length === 0 &&
            repositoryWorkflows.length === 0 && (
              <EmptyState
                icon={<WorkflowIcon className="size-5" />}
                title="No workflows"
                description="Import a repository workflow or configure a reusable template to start automating."
                actionHref={entitlement.canCreate ? "/workflows/new" : undefined}
                actionLabel={entitlement.canCreate ? "New Workflow" : undefined}
              />
            )}
          {repositoryWorkflows.length > 0 && (
            <section className="space-y-3">
              <div>
                <h2 className="font-semibold">Repository workflows</h2>
                <p className="text-muted-foreground text-sm">
                  Declarative pipelines imported from agent repositories. These definitions are
                  directly runnable and do not require a configured wrapper.
                </p>
              </div>
              <div className="grid gap-4">
                {repositoryWorkflows.map((workflow) => (
                  <div key={workflow.guid} className="rounded-lg border p-4">
                    <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
                      <div>
                        <div className="flex items-center gap-2">
                          <Link
                            href={`/workflows/${encodeURIComponent(workflow.slug)}/builder`}
                            className="font-semibold hover:underline"
                          >
                            {workflow.name}
                          </Link>
                          <Badge variant="outline" className="capitalize">
                            {workflow.patternKind.replace(/_/g, " ")}
                          </Badge>
                          <Badge variant={workflow.isEnabled ? "default" : "secondary"}>
                            {workflow.isEnabled ? "Enabled" : "Disabled"}
                          </Badge>
                        </div>
                        <div className="text-muted-foreground mt-1 flex flex-wrap gap-x-3 text-xs">
                          <Link
                            href={`/projects/${encodeURIComponent(workflow.projectSlug)}`}
                            className="hover:text-foreground hover:underline"
                          >
                            {workflow.projectTeamSlug}/{workflow.projectSlug}
                          </Link>
                          <span className="font-mono">{workflow.sourcePath}</span>
                        </div>
                      </div>
                      <div className="flex items-center gap-2">
                        {entitlement.canRun && (
                          <Button
                            size="sm"
                            variant="outline"
                            disabled={!workflow.isEnabled || busySlug === workflow.slug}
                            onClick={() => handleRunDefinition(workflow)}
                          >
                            <PlayIcon className="mr-1 size-3" /> Run
                          </Button>
                        )}
                        <Button asChild size="sm" variant="outline">
                          <Link href={`/workflows/${encodeURIComponent(workflow.slug)}/builder`}>
                            <WrenchIcon className="mr-1 size-3" /> Open
                          </Link>
                        </Button>
                      </div>
                    </div>
                    <WorkflowTopology stages={workflow.stages} />
                  </div>
                ))}
              </div>
            </section>
          )}
          {configuredWorkflows.length > 0 && (
            <section className="space-y-3">
              <div>
                <h2 className="font-semibold">Configured workflows</h2>
                <p className="text-muted-foreground text-sm">
                  Template-based workflows with custom inputs or triggers.
                </p>
              </div>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Name</TableHead>
                    <TableHead>Definition</TableHead>
                    <TableHead>Trigger</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Last run</TableHead>
                    <TableHead className="text-right">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {configuredWorkflows.map((wf) => {
                    const last = latestRun(wf.runs);
                    const busy = busySlug === wf.slug;
                    return (
                      <TableRow key={wf.slug}>
                        <TableCell>
                          <Link
                            href={`/workflows/${encodeURIComponent(wf.slug)}`}
                            className="font-medium hover:underline"
                          >
                            {wf.name}
                          </Link>
                          {wf.description && (
                            <div className="text-muted-foreground max-w-md truncate text-xs">
                              {wf.description}
                            </div>
                          )}
                        </TableCell>
                        <TableCell className="text-muted-foreground text-sm">
                          {wf.definitionName}
                        </TableCell>
                        <TableCell>
                          <Badge variant="outline">{formatTriggerKind(wf.triggerKind)}</Badge>
                          {wf.scheduleCron && (
                            <div className="text-muted-foreground mt-1 font-mono text-xs">
                              {wf.scheduleCron}
                            </div>
                          )}
                        </TableCell>
                        <TableCell>
                          <Badge variant={wf.isEnabled ? "default" : "secondary"}>
                            {wf.isEnabled ? "Enabled" : "Disabled"}
                          </Badge>
                        </TableCell>
                        <TableCell>
                          {last ? (
                            <div className="flex flex-col gap-1">
                              <RunStateBadge state={last.currentState} />
                              <span className="text-muted-foreground text-xs">
                                {new Date(last.startedAt).toLocaleString()}
                              </span>
                            </div>
                          ) : (
                            <span className="text-muted-foreground text-sm">Never run</span>
                          )}
                        </TableCell>
                        <TableCell className="text-right">
                          <div className="flex items-center justify-end gap-2">
                            {entitlement.canRun && (
                              <Button
                                variant="outline"
                                size="sm"
                                onClick={() => handleRunConfigured(wf)}
                                disabled={busy || !wf.isEnabled}
                              >
                                <PlayIcon className="mr-1 h-3 w-3" /> Run
                              </Button>
                            )}
                            {entitlement.canManage && (
                              <Button
                                variant="outline"
                                size="sm"
                                onClick={() => handleToggleConfigured(wf)}
                                disabled={busy}
                              >
                                {wf.isEnabled ? (
                                  <PowerOffIcon className="mr-1 h-3 w-3" />
                                ) : (
                                  <PowerIcon className="mr-1 h-3 w-3" />
                                )}
                                {wf.isEnabled ? "Disable" : "Enable"}
                              </Button>
                            )}
                            {entitlement.canManage && (
                              <Button
                                variant="outline"
                                size="sm"
                                onClick={() => handleDeleteConfigured(wf)}
                                disabled={busy}
                                className="text-destructive hover:text-destructive hover:bg-destructive/10"
                              >
                                <TrashIcon className="mr-1 h-3 w-3" /> Delete
                              </Button>
                            )}
                          </div>
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </section>
          )}
        </div>
      )}

      {/* Running — source pipelines plus fleet-wide Temporal monitoring */}
      {tab === "running" && (
        <div className="flex flex-col gap-4">
          <section className="space-y-3">
            <div>
              <h2 className="font-semibold">Agent workflow runs</h2>
              <p className="text-muted-foreground text-sm">
                Active repository-defined pipelines, with their project and current stage.
              </p>
            </div>
            {definitionRuns.loading && runningDefinitionRuns.length === 0 && (
              <div className="flex items-center justify-center p-8">
                <Loader2Icon className="text-muted-foreground size-5 animate-spin" />
              </div>
            )}
            {definitionRuns.error && (
              <div className="text-destructive bg-destructive/10 border-destructive/20 rounded-md border p-4 text-sm">
                Error: {definitionRuns.error.message}
              </div>
            )}
            {!definitionRuns.loading &&
              !definitionRuns.error &&
              runningDefinitionRuns.length === 0 && (
                <div className="text-muted-foreground rounded-md border border-dashed p-8 text-center text-sm">
                  No agent workflows are running.
                </div>
              )}
            {runningDefinitionRuns.length > 0 && <DefinitionRunList runs={runningDefinitionRuns} />}
          </section>
          {canViewPlatformRuns && (
            <section className="space-y-3">
              <div>
                <h2 className="font-semibold">Platform workflow instances</h2>
                <p className="text-muted-foreground text-sm">
                  Active Temporal operations across apps and platform services. Authorized operators
                  can cancel or terminate a stuck instance.
                </p>
              </div>
              <WorkflowInstancesPanel
                workflowType=""
                initialStatus="RUNNING"
                isAdmin={entitlement.canRun}
              />
            </section>
          )}
        </div>
      )}

      {/* Templates — complete WorkflowDefinition catalog */}
      {tab === "definitions" && (
        <div className="flex flex-col gap-4">
          {defsLoading && (
            <div className="flex items-center justify-center p-12">
              <Loader2Icon className="text-muted-foreground h-6 w-6 animate-spin" />
            </div>
          )}
          {defsError && (
            <div className="text-destructive bg-destructive/10 border-destructive/20 rounded-md border p-4 text-sm">
              Error: {defsError.message}
            </div>
          )}
          {!defsLoading && definitions.length === 0 && (
            <EmptyState
              icon={<WrenchIcon className="size-5" />}
              title="No workflow definitions"
              description="Workflow definitions let you model multi-step automation chains."
              actionHref="/workflows/builder"
              actionLabel="New Definition"
            />
          )}
          {definitions.length > 0 && (
            <ListControls controls={defsCtrl} searchPlaceholder="Search workflows…" />
          )}
          <div className="grid gap-4">
            {defsCtrl.rows.map((wf) => (
              <div
                key={wf.slug}
                className="flex items-center justify-between rounded-lg border p-4"
              >
                <div className="flex flex-col gap-1">
                  <div className="flex items-center gap-2">
                    <span className="font-medium">{wf.name}</span>
                    <Badge variant={wf.isEnabled ? "default" : "secondary"}>
                      {wf.isEnabled ? "Active" : "Disabled"}
                    </Badge>
                    <Badge variant="outline" className="text-xs">
                      {wf.patternKind}
                    </Badge>
                    {(wf.isGlobal || wf.organizationGuid == null) && (
                      <Badge variant="outline" className="text-xs">
                        Global
                      </Badge>
                    )}
                  </div>
                  <span className="text-muted-foreground text-sm">
                    {wf.description || "No description"}
                  </span>
                </div>
                <div className="flex items-center gap-4">
                  <div className="text-muted-foreground text-sm">
                    {wf.stageCount} {wf.stageCount === 1 ? "stage" : "stages"}
                  </div>
                  <Button variant="outline" size="sm" asChild>
                    <Link href={`/workflows/${encodeURIComponent(wf.slug)}/builder`}>
                      <WrenchIcon className="mr-1 h-3 w-3" /> Builder
                    </Link>
                  </Button>
                  {entitlement.canManage && (
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => handleDelete(wf)}
                      className="text-destructive hover:text-destructive hover:bg-destructive/10"
                    >
                      <TrashIcon className="mr-1 h-3 w-3" /> Delete
                    </Button>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* History — completed / failed / terminated workflow runs */}
      {tab === "history" && (
        <div className="flex flex-col gap-4">
          {((canViewPlatformRuns && runsLoading) || definitionRuns.loading) &&
            historyRuns.length === 0 &&
            historicalDefinitionRuns.length === 0 && (
              <div className="flex items-center justify-center p-12">
                <Loader2Icon className="text-muted-foreground h-6 w-6 animate-spin" />
              </div>
            )}
          {(!canViewPlatformRuns || !runsLoading) &&
            !definitionRuns.loading &&
            historyRuns.length === 0 &&
            historicalDefinitionRuns.length === 0 && (
              <EmptyState
                icon={<ClockIcon className="size-5" />}
                title="No workflow run history"
                description="Completed, failed, and terminated workflow runs appear here."
              />
            )}
          {historicalDefinitionRuns.length > 0 && (
            <section className="space-y-3">
              <div>
                <h2 className="font-semibold">Agent workflow runs</h2>
                <p className="text-muted-foreground text-sm">
                  Repository-defined pipeline executions, newest first.
                </p>
              </div>
              <DefinitionRunList runs={historicalDefinitionRuns} />
            </section>
          )}
          {canViewPlatformRuns && historyRuns.length > 0 && (
            <section className="space-y-3">
              <div>
                <h2 className="font-semibold">Platform workflow runs</h2>
                <p className="text-muted-foreground text-sm">
                  Deployment, provisioning, and other platform execution history.
                </p>
              </div>
              <div className="flex flex-col gap-2">
                {historyRuns.map((run) => (
                  <div
                    key={`${run.workflowId}-${run.runId}`}
                    className="flex items-center gap-3 rounded-md border px-4 py-3 text-sm"
                  >
                    <Badge
                      variant={run.status === "completed" ? "default" : "destructive"}
                      className="shrink-0"
                    >
                      {run.status}
                    </Badge>
                    <span className="min-w-0 flex-1 truncate font-mono text-xs font-medium">
                      {run.workflowKind}
                    </span>
                    {run.registeredAppId && (
                      <Badge variant="outline" className="shrink-0 text-xs">
                        {run.registeredAppId}
                      </Badge>
                    )}
                    <span className="text-muted-foreground shrink-0 text-xs">
                      {run.startedAt ? new Date(run.startedAt).toLocaleString() : "—"}
                    </span>
                  </div>
                ))}
              </div>
            </section>
          )}
        </div>
      )}
    </PageShell>
  );
}
