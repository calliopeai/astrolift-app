"use client";

import type { ReactNode } from "react";
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

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DataTable, type Column } from "@/components/data-table";
import { EmptyState } from "@/components/EmptyState";
import { ListControls } from "@/components/ListControls";
import { PageShell } from "@/components/PageShell";
import { RunStateBadge } from "@/components/screens/workflows/detail/RunStateBadge";
import { WorkflowTopology } from "@/components/workflows/workflow-topology";
import type {
  ConfiguredWorkflowWithRuns,
  WorkflowDefinitionRun,
  WorkflowDefinitionSummary,
} from "@/graphql/workflows/tiered.types";
import { useListControls } from "@/hooks/use-list-controls";

import type { useWorkflowsList, WorkflowTab } from "./use-workflows-list";
import { WORKFLOW_TABS } from "./use-workflows-list";
import {
  formatTriggerKind,
  latestRun,
} from "@/components/screens/workflows/detail/workflow-run-state";

const TAB_LABELS: Record<WorkflowTab, string> = {
  workflows: "Workflows",
  running: "Running",
  definitions: "Templates",
  history: "History",
};

export type WorkflowsListScreenProps = ReturnType<typeof useWorkflowsList> & {
  /** Platform Temporal instances panel, shown on the Running tab to audit readers. */
  instancesPanel?: ReactNode;
};

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
          {run.parentRunGuid && <Badge variant="outline">Nested · level {run.nestingDepth}</Badge>}
          {run.childRunCount > 0 && (
            <Badge variant="outline">{run.childRunCount} child run(s)</Badge>
          )}
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

/** /workflows: repository pipelines, configured workflows, runs, templates, history. */
export function WorkflowsListScreen({
  tab,
  setTab,
  entitlement,
  canViewPlatformRuns,
  definitions,
  defsLoading,
  defsError,
  repositoryWorkflows,
  table,
  busySlug,
  definitionRuns,
  historyRuns,
  runsLoading,
  onRunConfigured,
  onToggleConfigured,
  onDeleteConfigured,
  onRunDefinition,
  onDeleteDefinition,
  instancesPanel,
}: WorkflowsListScreenProps) {
  const wfLoading = table.state === "loading";
  const wfError = table.error;
  const runningDefinitionRuns = definitionRuns.running;
  const historicalDefinitionRuns = definitionRuns.historical;

  const workflowColumns: Column<ConfiguredWorkflowWithRuns>[] = [
    {
      id: "name",
      header: "Name",
      cell: (wf) => (
        <>
          <Link
            href={`/workflows/${encodeURIComponent(wf.slug)}`}
            className="font-medium hover:underline"
          >
            {wf.name}
          </Link>
          {wf.description && (
            <div className="text-muted-foreground max-w-md truncate text-xs">{wf.description}</div>
          )}
        </>
      ),
    },
    {
      id: "definition",
      header: "Definition",
      cellClassName: "text-muted-foreground text-sm",
      cell: (wf) => wf.definitionName,
    },
    {
      id: "trigger",
      header: "Trigger",
      cell: (wf) => (
        <>
          <Badge variant="outline">{formatTriggerKind(wf.triggerKind)}</Badge>
          {wf.scheduleCron && (
            <div className="text-muted-foreground mt-1 font-mono text-xs">{wf.scheduleCron}</div>
          )}
        </>
      ),
    },
    {
      id: "status",
      header: "Status",
      cell: (wf) => (
        <Badge variant={wf.isEnabled ? "default" : "secondary"}>
          {wf.isEnabled ? "Enabled" : "Disabled"}
        </Badge>
      ),
    },
    {
      id: "lastRun",
      header: "Last run",
      cell: (wf) => {
        const last = latestRun(wf.runs);
        return last ? (
          <div className="flex flex-col gap-1">
            <RunStateBadge state={last.currentState} />
            <span className="text-muted-foreground text-xs">
              {new Date(last.startedAt).toLocaleString()}
            </span>
          </div>
        ) : (
          <span className="text-muted-foreground text-sm">Never run</span>
        );
      },
    },
    {
      id: "actions",
      header: "Actions",
      align: "right",
      cell: (wf) => {
        const busy = busySlug === wf.slug;
        return (
          <div className="flex items-center justify-end gap-2">
            {entitlement.canRun && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => onRunConfigured(wf)}
                disabled={busy || !wf.isEnabled}
              >
                <PlayIcon className="mr-1 h-3 w-3" /> Run
              </Button>
            )}
            {entitlement.canManage && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => onToggleConfigured(wf)}
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
                onClick={() => onDeleteConfigured(wf)}
                disabled={busy}
                className="text-destructive hover:text-destructive hover:bg-destructive/10"
              >
                <TrashIcon className="mr-1 h-3 w-3" /> Delete
              </Button>
            )}
          </div>
        );
      },
    },
  ];

  // No sort: the definitions grid renders cards, not table rows, and a
  // sort control detached from a column header is a control with nothing
  // to point at.
  const defsCtrl = useListControls<WorkflowDefinitionSummary>({
    data: definitions,
    searchFn: (wf) => [wf.name, wf.description ?? "", wf.patternKind ?? ""].join(" "),
    initialPageSize: 25,
  });

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
        {WORKFLOW_TABS.map((t) => (
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
            (table.totalCount ?? 0) === 0 &&
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
            (table.totalCount ?? 0) === 0 &&
            !table.isFiltered &&
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
                            onClick={() => onRunDefinition(workflow)}
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
          {((table.totalCount ?? 0) > 0 || table.isFiltered) && (
            <section className="space-y-3">
              <div>
                <h2 className="font-semibold">Configured workflows</h2>
                <p className="text-muted-foreground text-sm">
                  Template-based workflows with custom inputs or triggers.
                </p>
              </div>
              <DataTable
                label="Configured workflows"
                controller={table}
                columns={workflowColumns}
                getRowId={(wf) => wf.slug}
                searchPlaceholder="Search by workflow or definition…"
                empty={{
                  icon: <WorkflowIcon className="size-5" />,
                  title: "No configured workflows",
                  description:
                    "Configure a reusable template to give it custom inputs or a trigger.",
                  actionHref: entitlement.canCreate ? "/workflows/new" : undefined,
                  actionLabel: entitlement.canCreate ? "New Workflow" : undefined,
                }}
                emptyFiltered={{
                  title: "No matching workflows",
                  description:
                    "No configured workflow matches that search. The server matches the workflow's name, slug and description, and the definition behind it.",
                }}
              />
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
              {instancesPanel}
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
                      onClick={() => onDeleteDefinition(wf)}
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
