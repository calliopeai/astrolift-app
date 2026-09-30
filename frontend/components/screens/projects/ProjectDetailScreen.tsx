"use client";

import {
  ActivityIcon,
  AlertTriangleIcon,
  BotIcon,
  BoxIcon,
  ChevronRightIcon,
  FileBoxIcon,
  PlusIcon,
  RocketIcon,
  Settings2Icon,
  ShieldIcon,
  Trash2Icon,
  UsersIcon,
  WorkflowIcon,
} from "lucide-react";
import Link from "next/link";
import * as React from "react";

import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { EmptyState } from "@/components/EmptyState";
import { ListSummary } from "@/components/list/ListSummary";
import { PageShell } from "@/components/PageShell";
import { StatusDot } from "@/components/StatusDot";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { StatTile } from "@/components/ui/stat-tile";
import { ProjectWorkflowTopology } from "@/components/workflows/workflow-topology";
import type {
  AstroliftAgentListItem,
  AstroliftAgentLiveStatus,
} from "@/graphql/agents/agents.types";
import type { ProvisioningStatus } from "@/graphql/registry/registry.types";
import type {
  WorkflowDefinitionRun,
  WorkflowDefinitionSummary,
} from "@/graphql/workflows/tiered.types";
import { formatRelativeAge } from "@/lib/format";
import { useFormatters } from "@/lib/i18n/formatters";

import type { useProjectDetail } from "./use-project-detail";

const statusDot: Record<ProvisioningStatus, "ok" | "warn" | "error" | "pending"> = {
  ready: "ok",
  pending: "warn",
  provisioning: "pending",
  failed: "error",
};

export type ProjectDetailScreenProps = ReturnType<typeof useProjectDetail>;

/**
 * Project overview: workload-aware stats, workflow topology, then agents,
 * apps and direct members as summaries of their top rows (list rule 3: an
 * overview holds no full table), each with "View all" to its own list, and
 * the settings sheet. Pure view; the data comes from useProjectDetail.
 */
export function ProjectDetailScreen({
  slug,
  project,
  loading,
  modulesLoading,
  canViewApps,
  canCreateApp,
  canViewAgents,
  canCreateAgent,
  canViewWorkflows,
  canManageMembers,
  apps: projectApps,
  appsLoading,
  appsLoaded,
  appsError,
  agents: projectAgents,
  agentsLoading,
  agentsLoaded,
  agentsError,
  liveAgents,
  workflows: projectWorkflows,
  workflowsLoading,
  workflowsLoaded,
  workflowsError,
  workflowRuns,
  directMembers,
  membersLoading,
  resourceCount,
  resourcesLoading,
  deleting,
  onDelete,
}: ProjectDetailScreenProps) {
  const fmt = useFormatters();
  const [settingsOpen, setSettingsOpen] = React.useState(false);
  const [confirmOpen, setConfirmOpen] = React.useState(false);
  const liveByWorkloadId = React.useMemo(() => {
    const map = new Map<string, AstroliftAgentLiveStatus>();
    for (const row of liveAgents) {
      map.set(row.workloadId, row);
    }
    return map;
  }, [liveAgents]);

  if (loading) {
    return (
      <PageShell title="Loading…">
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-48 w-full" />
      </PageShell>
    );
  }

  if (!project) {
    return (
      <PageShell title="Project not found">
        <EmptyState
          icon={<AlertTriangleIcon className="size-5" />}
          title={`No project with slug ${slug}`}
          description="It may have been soft-deleted, or you may not have permission to read it."
          actionHref="/administration/projects"
          actionLabel="Back to projects"
        />
      </PageShell>
    );
  }

  const activeApps = projectApps.filter((a) => a.provisioningStatus === "ready").length;
  const failingApps = projectApps.filter((a) => a.provisioningStatus === "failed").length;
  const hasAgents = projectAgents.length > 0;
  const hasApps = projectApps.length > 0;
  const hasWorkflows = projectWorkflows.length > 0;
  const agentQueryError = canViewAgents ? agentsError : false;
  const appsQueryError = canViewApps ? appsError : false;
  const workloadQueryError = appsQueryError || agentQueryError;
  const agentOnlyProject = hasAgents && canViewApps && !hasApps && !appsError;
  const mixedProject = hasAgents && hasApps;
  const workloadQueriesLoading =
    (canViewApps && appsLoading && !appsLoaded) ||
    modulesLoading ||
    (canViewAgents && agentsLoading && !agentsLoaded) ||
    (canViewWorkflows && workflowsLoading && !workflowsLoaded);
  const runningAgentRuns = projectAgents.reduce(
    (total, agent) => total + (liveByWorkloadId.get(agent.id)?.runningCount ?? agent.runningCount),
    0
  );
  const agentsNeedingAttention = projectAgents.filter((agent) => {
    const live = liveByWorkloadId.get(agent.id);
    const status = (live?.lastRunStatus ?? agent.lastRunStatus ?? "").toLowerCase();
    return (live?.isPaused ?? agent.runPaused) || ["failed", "timed_out"].includes(status);
  }).length;
  const sourceRepoCount = new Set(projectAgents.map((agent) => agent.sourceRepo).filter(Boolean))
    .size;
  const workflowMembershipByAgent = (() => {
    const membership = new Map<string, WorkflowDefinitionSummary[]>();
    for (const workflow of projectWorkflows) {
      for (const stage of workflow.stages) {
        const agentSlug = stage.agentSlug || stage.agentRef;
        if (!agentSlug) continue;
        const workflowsForAgent = membership.get(agentSlug) ?? [];
        if (!workflowsForAgent.some((candidate) => candidate.guid === workflow.guid)) {
          workflowsForAgent.push(workflow);
        }
        membership.set(agentSlug, workflowsForAgent);
      }
    }
    return membership;
  })();
  const latestWorkflowRunByDefinition = (() => {
    const latest = new Map<string, WorkflowDefinitionRun>();
    for (const run of workflowRuns) {
      if (!latest.has(run.definitionGuid)) latest.set(run.definitionGuid, run);
    }
    return latest;
  })();
  const activeWorkflowRuns = workflowRuns.filter((run) => run.status === "running").length;
  const workflowsNeedingAttention = [...latestWorkflowRunByDefinition.values()].filter((run) =>
    ["failed", "timed_out"].includes(run.status)
  ).length;
  const workflowStageStatuses = (workflow: WorkflowDefinitionSummary): Record<number, string> => {
    const run = latestWorkflowRunByDefinition.get(workflow.guid);
    if (!run) return {};
    if (["succeeded", "completed"].includes(run.status)) {
      return Object.fromEntries(workflow.stages.map((stage) => [stage.order, "succeeded"]));
    }
    const current = run.currentStageOrder;
    if (current == null) {
      return Object.fromEntries(workflow.stages.map((stage) => [stage.order, run.status]));
    }
    return Object.fromEntries(
      workflow.stages.map((stage) => [
        stage.order,
        stage.order < current
          ? "succeeded"
          : stage.order > current
            ? "pending"
            : ["failed", "timed_out"].includes(run.status)
              ? run.status
              : "running",
      ])
    );
  };

  return (
    <PageShell
      title={
        <span className="flex items-center gap-2">
          {agentOnlyProject ? <BotIcon className="size-5" /> : <FileBoxIcon className="size-5" />}
          {project.name}
          {!workloadQueriesLoading && agentOnlyProject && (
            <Badge variant="secondary">
              {hasWorkflows ? "Agent workflow project" : "Agent project"}
            </Badge>
          )}
          {!workloadQueriesLoading && mixedProject && (
            <Badge variant="secondary">Apps + agents</Badge>
          )}
        </span>
      }
      description={
        <nav aria-label="breadcrumb" className="text-muted-foreground text-sm">
          <ol className="flex flex-wrap items-center gap-1">
            <li>
              <Link href="/administration/teams" className="hover:text-foreground hover:underline">
                {project.organization.slug}
              </Link>
            </li>
            <li>
              <ChevronRightIcon className="size-3" />
            </li>
            <li>
              <Link href="/administration/teams" className="hover:text-foreground hover:underline">
                {project.team.slug}
              </Link>
            </li>
            <li>
              <ChevronRightIcon className="size-3" />
            </li>
            <li className="text-foreground font-mono text-xs">{project.slug}</li>
          </ol>
        </nav>
      }
      actions={
        <>
          {!workloadQueriesLoading && hasAgents && (
            <Button asChild variant="outline">
              <Link href={`/agents?project=${encodeURIComponent(project.slug)}`}>
                <ActivityIcon className="size-4" /> Agent activity
              </Link>
            </Button>
          )}
          {!workloadQueriesLoading && hasWorkflows && (
            <Button asChild variant="outline">
              <Link href="/workflows">
                <WorkflowIcon className="size-4" /> Workflows
              </Link>
            </Button>
          )}
          {!workloadQueriesLoading && agentOnlyProject && canCreateAgent ? (
            <Button asChild>
              <Link href="/agents/new">
                <PlusIcon className="size-4" /> Register agent repo
              </Link>
            </Button>
          ) : !workloadQueriesLoading && hasApps && canCreateApp ? (
            <Button asChild variant="outline">
              <Link href="/apps/new">
                <PlusIcon className="size-4" /> New app
              </Link>
            </Button>
          ) : null}
          <Button variant="outline" onClick={() => setSettingsOpen(true)}>
            <Settings2Icon className="size-4" /> Settings
          </Button>
        </>
      }
    >
      {/* ─── stats strip ───────────────────────────────────────────────── */}
      {workloadQueriesLoading ? (
        <div className="grid gap-4 sm:grid-cols-3">
          <Skeleton className="h-36 w-full" />
          <Skeleton className="h-36 w-full" />
          <Skeleton className="h-36 w-full" />
        </div>
      ) : hasAgents || hasWorkflows ? (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <StatTile
            icon={BotIcon}
            label="Registered agents"
            value={projectAgents.length}
            footer={`${sourceRepoCount} source ${sourceRepoCount === 1 ? "repo" : "repos"}`}
            href="#agents"
          />
          {canViewWorkflows && (
            <StatTile
              icon={WorkflowIcon}
              label="Workflows"
              value={projectWorkflows.length}
              footer={
                activeWorkflowRuns > 0
                  ? `${activeWorkflowRuns} active ${activeWorkflowRuns === 1 ? "run" : "runs"}`
                  : `${projectWorkflows.reduce((count, workflow) => count + workflow.stageCount, 0)} total stages`
              }
              href="#workflows"
            />
          )}
          {mixedProject && (
            <StatTile
              icon={RocketIcon}
              label="Registered apps"
              value={projectApps.length}
              footer={failingApps > 0 ? `${failingApps} failing` : `${activeApps} ready`}
              className={failingApps > 0 ? "border-warning-border bg-warning/5" : undefined}
              href="#apps"
            />
          )}
          <StatTile
            icon={ActivityIcon}
            label="Active agent runs"
            value={runningAgentRuns}
            footer={runningAgentRuns === 1 ? "run in progress" : "runs in progress"}
            href={`/agents?project=${encodeURIComponent(project.slug)}`}
          />
          <StatTile
            icon={AlertTriangleIcon}
            label="Needs attention"
            value={agentsNeedingAttention + workflowsNeedingAttention}
            footer="paused or latest agent/workflow run failed"
            className={
              agentsNeedingAttention + workflowsNeedingAttention > 0
                ? "border-warning-border bg-warning/5"
                : undefined
            }
            href={
              hasWorkflows ? "#workflows" : `/agents?project=${encodeURIComponent(project.slug)}`
            }
          />
          <StatTile
            icon={BoxIcon}
            label="Project resources"
            value={resourceCount}
            footer="infrastructure and shared secret bundles"
            loading={resourcesLoading}
            href={`/projects/${encodeURIComponent(project.slug)}/resources`}
          />
        </div>
      ) : hasApps ? (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <StatTile
            icon={RocketIcon}
            label="Registered apps"
            value={projectApps.length}
            footer={failingApps > 0 ? `${failingApps} failing` : `${activeApps} ready`}
            className={failingApps > 0 ? "border-warning-border bg-warning/5" : undefined}
            href="#apps"
          />
          <StatTile
            icon={BoxIcon}
            label="Active deployments"
            value={activeApps}
            footer="apps with a healthy latest rollout"
            href="#apps"
          />
          <StatTile
            icon={BoxIcon}
            label="Project resources"
            value={resourceCount}
            footer="infrastructure and shared secret bundles"
            loading={resourcesLoading}
            href={`/projects/${encodeURIComponent(project.slug)}/resources`}
          />
          {canManageMembers && (
            <StatTile
              icon={UsersIcon}
              label="Direct members"
              value={directMembers.length}
              footer="users granted project-scope access"
              href="#members"
            />
          )}
        </div>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          <StatTile
            icon={FileBoxIcon}
            label={
              workloadQueryError || !canViewAgents ? "Visible workloads" : "Registered workloads"
            }
            value={0}
            footer={
              workloadQueryError
                ? "some workload data could not be loaded"
                : canViewAgents
                  ? "apps and agents in this project"
                  : "workloads available to your modules"
            }
            className={workloadQueryError ? "border-warning-border bg-warning/5" : undefined}
            href="/apps"
          />
          <StatTile
            icon={BoxIcon}
            label="Project resources"
            value={resourceCount}
            footer="infrastructure and shared secret bundles"
            loading={resourcesLoading}
            href={`/projects/${encodeURIComponent(project.slug)}/resources`}
          />
          {canManageMembers && (
            <StatTile
              icon={UsersIcon}
              label="Direct members"
              value={directMembers.length}
              footer="users granted project-scope access"
              href="#members"
            />
          )}
        </div>
      )}

      {/* ─── agent workloads ──────────────────────────────────────────── */}
      {workloadQueryError && (
        <Card className="border-danger-border bg-danger-bg">
          <CardContent className="flex items-start gap-3 p-6">
            <AlertTriangleIcon className="text-danger-fg mt-0.5 size-5 shrink-0" />
            <div>
              <p className="text-danger-fg font-medium">Workload summary incomplete</p>
              <p className="text-muted-foreground mt-1 text-sm">
                {appsQueryError && agentQueryError
                  ? "Astrolift could not load application or agent workloads for this project."
                  : appsQueryError
                    ? "Application workloads could not be loaded. Agent data is shown, but this project has not been classified as agent-only."
                    : "Agent workloads could not be loaded. Application data is shown, but this project has not been classified as app-only."}
              </p>
            </div>
          </CardContent>
        </Card>
      )}

      {canViewWorkflows && workflowsError && (
        <Card className="border-danger-border bg-danger-bg">
          <CardContent className="flex items-start gap-3 p-6">
            <AlertTriangleIcon className="text-danger-fg mt-0.5 size-5 shrink-0" />
            <div>
              <p className="text-danger-fg font-medium">Workflow topology unavailable</p>
              <p className="text-muted-foreground mt-1 text-sm">
                Agents are still shown, but Astrolift could not load their workflow membership.
              </p>
            </div>
          </CardContent>
        </Card>
      )}

      {hasWorkflows && (
        <Card id="workflows" className="scroll-mt-20">
          <CardHeader className="flex flex-row items-start justify-between gap-3 space-y-0">
            <div>
              <CardTitle className="flex items-center gap-2 text-base">
                <WorkflowIcon className="size-4" /> Workflow topology
              </CardTitle>
              <CardDescription>
                Repository-declared pipelines, bound agents, and latest run state.
              </CardDescription>
            </div>
            <Button asChild size="sm" variant="outline">
              <Link href="/workflows">View all</Link>
            </Button>
          </CardHeader>
          <CardContent>
            <ProjectWorkflowTopology
              workflows={projectWorkflows}
              runStatusByWorkflow={Object.fromEntries(
                projectWorkflows.flatMap((workflow) => {
                  const run = latestWorkflowRunByDefinition.get(workflow.guid);
                  return run ? [[workflow.guid, run.status]] : [];
                })
              )}
              stageStatusByWorkflow={Object.fromEntries(
                projectWorkflows.map((workflow) => [workflow.guid, workflowStageStatuses(workflow)])
              )}
              height={230}
            />
          </CardContent>
        </Card>
      )}

      {hasAgents && (
        <ListSummary<AstroliftAgentListItem>
          className="scroll-mt-20"
          icon={<BotIcon className="size-4" />}
          title="Agents in this project"
          description={`Registered agent workloads for ${project.team.slug}/${project.slug}. Live status refreshes automatically.`}
          count={projectAgents.length}
          rows={projectAgents}
          keyOf={(agent) => agent.id}
          rowHref={(agent) => `/agents/${encodeURIComponent(agent.slug)}`}
          viewAllHref={`/agents?project=${encodeURIComponent(project.slug)}`}
          renderRow={(agent) => {
            const usedBy = workflowMembershipByAgent.get(agent.slug) ?? [];
            const live = liveByWorkloadId.get(agent.id);
            return (
              <span className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-medium">{agent.name}</span>
                  <span className="text-muted-foreground block truncate font-mono text-xs">
                    {agent.slug} · {formatAgentRunMode(agent.runFamily, agent.runMode)}
                  </span>
                </span>
                {usedBy.length > 0 ? (
                  <Badge variant="secondary" className="max-w-48 truncate font-mono">
                    {usedBy[0]!.slug}
                    {usedBy.length > 1 ? ` +${usedBy.length - 1}` : ""}
                  </Badge>
                ) : (
                  <Badge variant="outline">Standalone</Badge>
                )}
                <AgentLiveStatus agent={agent} live={live} />
                <AgentLastRun
                  status={live?.lastRunStatus ?? agent.lastRunStatus}
                  at={live?.lastRunAt ?? agent.lastRunAt}
                />
              </span>
            );
          }}
        />
      )}

      {!workloadQueriesLoading &&
        !hasAgents &&
        !hasApps &&
        !hasWorkflows &&
        !workloadQueryError && (
          <Card>
            <CardContent className="flex flex-col items-center gap-4 p-10 text-center">
              <div className="bg-muted flex size-11 items-center justify-center rounded-full">
                <FileBoxIcon className="text-muted-foreground size-5" />
              </div>
              <div>
                <h2 className="font-semibold">
                  {canViewAgents ? "No workloads in this project" : "No visible workloads"}
                </h2>
                <p className="text-muted-foreground mt-1 max-w-lg text-sm">
                  {canViewAgents
                    ? "Connect an agent repository or register an application. Astrolift will adapt this overview to the workload types you add."
                    : "No application workloads are visible in this project. Other workload types may be hidden by your module access."}
                </p>
              </div>
              <div className="flex flex-wrap justify-center gap-2">
                {canCreateAgent && (
                  <Button asChild>
                    <Link href="/agents/new">
                      <BotIcon className="size-4" /> Register agent repo
                    </Link>
                  </Button>
                )}
                {canCreateApp && (
                  <Button asChild variant="outline">
                    <Link href="/apps/new">
                      <RocketIcon className="size-4" /> Register app
                    </Link>
                  </Button>
                )}
              </div>
            </CardContent>
          </Card>
        )}

      {/* ─── app workloads ────────────────────────────────────────────── */}
      {hasApps && (
        <ListSummary<(typeof projectApps)[number]>
          className="scroll-mt-20"
          icon={<RocketIcon className="size-4" />}
          title="Apps in this project"
          description={`Registered apps associated with ${project.team.slug}/${project.slug}.`}
          count={projectApps.length}
          rows={projectApps}
          keyOf={(a) => a.id}
          rowHref={(a) => `/apps/${a.slug}`}
          viewAllHref={`/apps?project=${encodeURIComponent(project.slug)}`}
          loading={appsLoading && projectApps.length === 0}
          renderRow={(a) => (
            <span className="flex min-w-0 items-center gap-3">
              <StatusDot status={statusDot[a.provisioningStatus]} className="shrink-0" />
              <span className="min-w-0 flex-1">
                <span className="block truncate font-medium">{a.name}</span>
                <span className="text-muted-foreground block truncate font-mono text-xs">
                  {a.slug}
                  {a.sourceRepo ? ` · ${a.sourceRepo}` : ""}
                </span>
              </span>
              <Badge variant="secondary" className="shrink-0 capitalize">
                {a.provisioningStatus}
              </Badge>
              <span className="text-muted-foreground shrink-0 font-mono text-xs">
                {fmt.formatDate(a.createdAt)}
              </span>
            </span>
          )}
        />
      )}

      {/* ─── members ───────────────────────────────────────────────────── */}
      {canManageMembers && (
        <ListSummary<(typeof directMembers)[number]>
          className="scroll-mt-20"
          icon={<ShieldIcon className="size-4" />}
          title="Project members"
          description="Members with explicit access granted at the project scope. Org-wide and team-wide members are not listed here."
          count={directMembers.length}
          rows={directMembers}
          keyOf={(m) => m.id}
          viewAllHref="/administration/members"
          loading={membersLoading && directMembers.length === 0}
          empty={{
            icon: <UsersIcon className="size-5" />,
            title: "No direct project members",
            description:
              "Anyone with team or org-wide access already sees this project. Grant explicit project-scope access from the Members page.",
            actionHref: "/administration/members",
            actionLabel: "Manage members",
          }}
          renderRow={(m) => (
            <span className="flex min-w-0 items-center justify-between gap-3">
              <span className="min-w-0">
                <span className="block truncate font-medium">
                  {m.user.username || m.user.email}
                </span>
                <span className="text-muted-foreground block truncate text-xs">
                  {m.user.email} · joined {m.joinedAt ? fmt.formatDate(m.joinedAt) : "—"}
                </span>
              </span>
              <Badge variant="outline" className="shrink-0 text-xs uppercase">
                {m.scopeKind}
              </Badge>
            </span>
          )}
        />
      )}

      {/* ─── settings sheet ────────────────────────────────────────────── */}
      <Sheet open={settingsOpen} onOpenChange={setSettingsOpen}>
        <SheetContent side="right" className="sm:max-w-md">
          <SheetHeader>
            <SheetTitle>Project settings</SheetTitle>
            <SheetDescription>
              Configure or remove {project.team.slug}/{project.slug}.
            </SheetDescription>
          </SheetHeader>
          <div className="space-y-6 px-4 py-4">
            <section className="space-y-2">
              <h3 className="text-sm font-medium">Identity</h3>
              <dl className="grid grid-cols-3 gap-x-3 gap-y-1.5 text-sm">
                <dt className="text-muted-foreground">Name</dt>
                <dd className="col-span-2">{project.name}</dd>
                <dt className="text-muted-foreground">Slug</dt>
                <dd className="col-span-2 font-mono text-xs">{project.slug}</dd>
                <dt className="text-muted-foreground">Team</dt>
                <dd className="col-span-2 font-mono text-xs">{project.team.slug}</dd>
                <dt className="text-muted-foreground">Created</dt>
                <dd className="col-span-2 text-xs">{fmt.formatDateTime(project.createdAt)}</dd>
              </dl>
              <p className="text-muted-foreground text-xs">
                Project rename ships when the backend mutation lands; the slug is intentionally
                immutable so downstream cost allocation stays stable across rename.
              </p>
            </section>
            <section className="space-y-2">
              <h3 className="text-destructive text-sm font-medium">Danger zone</h3>
              <p className="text-muted-foreground text-xs">
                Soft delete removes this project from listings. Workloads attached to it remain
                visible until reassigned.
              </p>
              <Can permission="project.delete">
                <Button
                  variant="outline"
                  className="text-destructive border-destructive/40"
                  disabled={deleting}
                  onClick={() => setConfirmOpen(true)}
                >
                  <Trash2Icon className="size-4" />
                  Delete project
                </Button>
              </Can>
            </section>
          </div>
          <SheetFooter>
            <Button variant="outline" onClick={() => setSettingsOpen(false)}>
              Close
            </Button>
          </SheetFooter>
        </SheetContent>
      </Sheet>

      <ConfirmDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        title={`Delete project ${project.team.slug}/${project.slug}?`}
        description="Soft delete — apps and agents remain visible until you reassign them. The slug becomes reclaimable."
        confirmLabel="Delete project"
        destructive
        onConfirm={onDelete}
      />
    </PageShell>
  );
}

const RUN_FAMILY_LABELS: Record<string, string> = {
  task: "Task",
  service: "Service",
};

const RUN_MODE_LABELS: Record<string, string> = {
  once: "Once",
  loop: "Loop",
  schedule: "Schedule",
  trigger: "Trigger",
  service: "Service",
};

const AGENT_RUN_STATUS_DOT: Record<string, "ok" | "warn" | "error" | "muted" | "pending"> = {
  running: "pending",
  queued: "warn",
  completed: "ok",
  succeeded: "ok",
  failed: "error",
  timed_out: "error",
  cancelled: "muted",
  canceled: "muted",
};

function titleCase(value: string): string {
  return value
    .replace(/[_-]+/g, " ")
    .split(" ")
    .filter(Boolean)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
    .join(" ");
}

function formatAgentRunMode(runFamily: string, runMode: string): string {
  const family = RUN_FAMILY_LABELS[runFamily.toLowerCase()] ?? titleCase(runFamily);
  const mode = RUN_MODE_LABELS[runMode.toLowerCase()] ?? titleCase(runMode);
  if (!mode || mode === family) return family || "—";
  return `${family} · ${mode}`;
}

function AgentLiveStatus({
  agent,
  live,
}: {
  agent: AstroliftAgentListItem;
  live: AstroliftAgentLiveStatus | undefined;
}) {
  const runningCount = live?.runningCount ?? agent.runningCount;
  const isPaused = live?.isPaused ?? agent.runPaused;

  if (runningCount > 0) {
    return (
      <span className="inline-flex items-center gap-1.5">
        <StatusDot status="pending" />
        <Badge>{runningCount} running</Badge>
      </span>
    );
  }
  if (isPaused) {
    return (
      <span className="inline-flex items-center gap-1.5">
        <StatusDot status="muted" />
        <Badge variant="outline">Paused</Badge>
      </span>
    );
  }
  if (live?.nextScheduledAt) {
    return (
      <span className="inline-flex items-center gap-1.5">
        <StatusDot status="warn" />
        <Badge variant="secondary" title={live.nextScheduledAt}>
          Next {formatRelativeAge(live.nextScheduledAt)}
        </Badge>
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1.5">
      <StatusDot status="muted" />
      <Badge variant="outline">Idle</Badge>
    </span>
  );
}

function AgentLastRun({
  status,
  at,
}: {
  status: string | null | undefined;
  at: string | null | undefined;
}) {
  if (!status && !at) {
    return <span className="text-muted-foreground text-sm">Never run</span>;
  }
  const dot = AGENT_RUN_STATUS_DOT[(status ?? "").toLowerCase()] ?? "muted";
  return (
    <span className="inline-flex items-center gap-1.5">
      {status && (
        <>
          <StatusDot status={dot} />
          <Badge variant={dot === "error" ? "destructive" : "secondary"}>{titleCase(status)}</Badge>
        </>
      )}
      {at && (
        <span className="text-muted-foreground text-xs" title={at}>
          {formatRelativeAge(at)}
        </span>
      )}
    </span>
  );
}
