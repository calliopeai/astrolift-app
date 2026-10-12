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
import { useTranslations } from "next-intl";
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
  const t = useTranslations("projectDetail");
  const appStatus = useTranslations("apps.frame.status");
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
      <PageShell title={t("loading")}>
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-48 w-full" />
      </PageShell>
    );
  }

  if (!project) {
    return (
      <PageShell title={t("notFound")}>
        <EmptyState
          icon={<AlertTriangleIcon className="size-5" />}
          title={t("notFoundTitle", { slug })}
          description={t("notFoundDescription")}
          actionHref="/administration/projects"
          actionLabel={t("backToProjects")}
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
              {hasWorkflows ? t("agentWorkflowProject") : t("agentProject")}
            </Badge>
          )}
          {!workloadQueriesLoading && mixedProject && (
            <Badge variant="secondary">{t("appsAndAgents")}</Badge>
          )}
        </span>
      }
      description={
        <nav aria-label={t("breadcrumb")} className="text-muted-foreground text-sm">
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
                <ActivityIcon className="size-4" /> {t("agentActivity")}
              </Link>
            </Button>
          )}
          {!workloadQueriesLoading && hasWorkflows && (
            <Button asChild variant="outline">
              <Link href="/workflows">
                <WorkflowIcon className="size-4" /> {t("workflows")}
              </Link>
            </Button>
          )}
          {!workloadQueriesLoading && agentOnlyProject && canCreateAgent ? (
            <Button asChild>
              <Link href="/agents/new">
                <PlusIcon className="size-4" /> {t("registerAgentRepo")}
              </Link>
            </Button>
          ) : !workloadQueriesLoading && hasApps && canCreateApp ? (
            <Button asChild variant="outline">
              <Link href="/apps/new">
                <PlusIcon className="size-4" /> {t("newApp")}
              </Link>
            </Button>
          ) : null}
          <Button variant="outline" onClick={() => setSettingsOpen(true)}>
            <Settings2Icon className="size-4" /> {t("settings")}
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
            label={t("registeredAgents")}
            value={projectAgents.length}
            footer={t("sourceRepos", { count: sourceRepoCount })}
            href="#agents"
          />
          {canViewWorkflows && (
            <StatTile
              icon={WorkflowIcon}
              label={t("workflows")}
              value={projectWorkflows.length}
              footer={
                activeWorkflowRuns > 0
                  ? t("activeRuns", { count: activeWorkflowRuns })
                  : t("totalStages", {
                      count: projectWorkflows.reduce(
                        (count, workflow) => count + workflow.stageCount,
                        0
                      ),
                    })
              }
              href="#workflows"
            />
          )}
          {mixedProject && (
            <StatTile
              icon={RocketIcon}
              label={t("registeredApps")}
              value={projectApps.length}
              footer={
                failingApps > 0
                  ? t("failingApps", { count: failingApps })
                  : t("readyApps", { count: activeApps })
              }
              className={failingApps > 0 ? "border-warning-border bg-warning/5" : undefined}
              href="#apps"
            />
          )}
          <StatTile
            icon={ActivityIcon}
            label={t("activeAgentRuns")}
            value={runningAgentRuns}
            footer={t("runsInProgress", { count: runningAgentRuns })}
            href={`/agents?project=${encodeURIComponent(project.slug)}`}
          />
          <StatTile
            icon={AlertTriangleIcon}
            label={t("needsAttention")}
            value={agentsNeedingAttention + workflowsNeedingAttention}
            footer={t("needsAttentionFooter")}
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
            label={t("projectResources")}
            value={resourceCount}
            footer={t("projectResourcesFooter")}
            loading={resourcesLoading}
            href={`/projects/${encodeURIComponent(project.slug)}/resources`}
          />
        </div>
      ) : hasApps ? (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <StatTile
            icon={RocketIcon}
            label={t("registeredApps")}
            value={projectApps.length}
            footer={
              failingApps > 0
                ? t("failingApps", { count: failingApps })
                : t("readyApps", { count: activeApps })
            }
            className={failingApps > 0 ? "border-warning-border bg-warning/5" : undefined}
            href="#apps"
          />
          <StatTile
            icon={BoxIcon}
            label={t("activeDeployments")}
            value={activeApps}
            footer={t("activeDeploymentsFooter")}
            href="#apps"
          />
          <StatTile
            icon={BoxIcon}
            label={t("projectResources")}
            value={resourceCount}
            footer={t("projectResourcesFooter")}
            loading={resourcesLoading}
            href={`/projects/${encodeURIComponent(project.slug)}/resources`}
          />
          {canManageMembers && (
            <StatTile
              icon={UsersIcon}
              label={t("directMembers")}
              value={directMembers.length}
              footer={t("directMembersFooter")}
              href="#members"
            />
          )}
        </div>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          <StatTile
            icon={FileBoxIcon}
            label={
              workloadQueryError || !canViewAgents
                ? t("visibleWorkloads")
                : t("registeredWorkloads")
            }
            value={0}
            footer={
              workloadQueryError
                ? t("workloadsPartial")
                : canViewAgents
                  ? t("workloadsAppsAndAgents")
                  : t("workloadsForModules")
            }
            className={workloadQueryError ? "border-warning-border bg-warning/5" : undefined}
            href="/apps"
          />
          <StatTile
            icon={BoxIcon}
            label={t("projectResources")}
            value={resourceCount}
            footer={t("projectResourcesFooter")}
            loading={resourcesLoading}
            href={`/projects/${encodeURIComponent(project.slug)}/resources`}
          />
          {canManageMembers && (
            <StatTile
              icon={UsersIcon}
              label={t("directMembers")}
              value={directMembers.length}
              footer={t("directMembersFooter")}
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
              <p className="text-danger-fg font-medium">{t("summaryIncomplete")}</p>
              <p className="text-muted-foreground mt-1 text-sm">
                {appsQueryError && agentQueryError
                  ? t("summaryBothFailed")
                  : appsQueryError
                    ? t("summaryAppsFailed")
                    : t("summaryAgentsFailed")}
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
              <p className="text-danger-fg font-medium">{t("topologyUnavailable")}</p>
              <p className="text-muted-foreground mt-1 text-sm">
                {t("topologyUnavailableDescription")}
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
                <WorkflowIcon className="size-4" /> {t("topology")}
              </CardTitle>
              <CardDescription>{t("topologyDescription")}</CardDescription>
            </div>
            <Button asChild size="sm" variant="outline">
              <Link href="/workflows">{t("viewAll")}</Link>
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
          title={t("agentsTitle")}
          description={t("agentsDescription", { team: project.team.slug, project: project.slug })}
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
                    {agent.slug} · {formatAgentRunMode(agent.runFamily, agent.runMode, t)}
                  </span>
                </span>
                {usedBy.length > 0 ? (
                  <Badge variant="secondary" className="max-w-48 truncate font-mono">
                    {usedBy[0]!.slug}
                    {usedBy.length > 1 ? ` +${usedBy.length - 1}` : ""}
                  </Badge>
                ) : (
                  <Badge variant="outline">{t("standalone")}</Badge>
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
                  {canViewAgents ? t("noWorkloads") : t("noVisibleWorkloads")}
                </h2>
                <p className="text-muted-foreground mt-1 max-w-lg text-sm">
                  {canViewAgents ? t("noWorkloadsDescription") : t("noVisibleWorkloadsDescription")}
                </p>
              </div>
              <div className="flex flex-wrap justify-center gap-2">
                {canCreateAgent && (
                  <Button asChild>
                    <Link href="/agents/new">
                      <BotIcon className="size-4" /> {t("registerAgentRepo")}
                    </Link>
                  </Button>
                )}
                {canCreateApp && (
                  <Button asChild variant="outline">
                    <Link href="/apps/new">
                      <RocketIcon className="size-4" /> {t("registerApp")}
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
          title={t("appsTitle")}
          description={t("appsDescription", { team: project.team.slug, project: project.slug })}
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
                {appStatus.has(a.provisioningStatus)
                  ? appStatus(a.provisioningStatus)
                  : a.provisioningStatus}
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
          title={t("membersTitle")}
          description={t("membersDescription")}
          count={directMembers.length}
          rows={directMembers}
          keyOf={(m) => m.id}
          viewAllHref="/administration/members"
          loading={membersLoading && directMembers.length === 0}
          empty={{
            icon: <UsersIcon className="size-5" />,
            title: t("noMembers"),
            description: t("noMembersDescription"),
            actionHref: "/administration/members",
            actionLabel: t("manageMembers"),
          }}
          renderRow={(m) => (
            <span className="flex min-w-0 items-center justify-between gap-3">
              <span className="min-w-0">
                <span className="block truncate font-medium">
                  {m.user.username || m.user.email}
                </span>
                <span className="text-muted-foreground block truncate text-xs">
                  {m.user.email} ·{" "}
                  {t("joined", { date: m.joinedAt ? fmt.formatDate(m.joinedAt) : "—" })}
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
            <SheetTitle>{t("settingsTitle")}</SheetTitle>
            <SheetDescription>
              {t("settingsDescription", { team: project.team.slug, project: project.slug })}
            </SheetDescription>
          </SheetHeader>
          <div className="space-y-6 px-4 py-4">
            <section className="space-y-2">
              <h3 className="text-sm font-medium">{t("identity")}</h3>
              <dl className="grid grid-cols-3 gap-x-3 gap-y-1.5 text-sm">
                <dt className="text-muted-foreground">{t("name")}</dt>
                <dd className="col-span-2">{project.name}</dd>
                <dt className="text-muted-foreground">{t("slug")}</dt>
                <dd className="col-span-2 font-mono text-xs">{project.slug}</dd>
                <dt className="text-muted-foreground">{t("team")}</dt>
                <dd className="col-span-2 font-mono text-xs">{project.team.slug}</dd>
                <dt className="text-muted-foreground">{t("created")}</dt>
                <dd className="col-span-2 text-xs">{fmt.formatDateTime(project.createdAt)}</dd>
              </dl>
              <p className="text-muted-foreground text-xs">{t("renameNote")}</p>
            </section>
            <section className="space-y-2">
              <h3 className="text-destructive text-sm font-medium">{t("dangerZone")}</h3>
              <p className="text-muted-foreground text-xs">{t("dangerZoneDescription")}</p>
              <Can permission="project.delete">
                <Button
                  variant="outline"
                  className="text-destructive border-destructive/40"
                  disabled={deleting}
                  onClick={() => setConfirmOpen(true)}
                >
                  <Trash2Icon className="size-4" />
                  {t("deleteProject")}
                </Button>
              </Can>
            </section>
          </div>
          <SheetFooter>
            <Button variant="outline" onClick={() => setSettingsOpen(false)}>
              {t("close")}
            </Button>
          </SheetFooter>
        </SheetContent>
      </Sheet>

      <ConfirmDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        title={t("deleteTitle", { team: project.team.slug, project: project.slug })}
        description={t("deleteDescription")}
        confirmLabel={t("deleteProject")}
        destructive
        onConfirm={onDelete}
      />
    </PageShell>
  );
}

const RUN_FAMILY_KEYS: Record<string, string> = {
  task: "runFamily.task",
  service: "runFamily.service",
};

const RUN_MODE_KEYS: Record<string, string> = {
  once: "runMode.once",
  loop: "runMode.loop",
  schedule: "runMode.schedule",
  trigger: "runMode.trigger",
  service: "runMode.service",
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

function formatAgentRunMode(
  runFamily: string,
  runMode: string,
  t: (key: string) => string
): string {
  const familyKey = RUN_FAMILY_KEYS[runFamily.toLowerCase()];
  const modeKey = RUN_MODE_KEYS[runMode.toLowerCase()];
  const family = familyKey ? t(familyKey) : titleCase(runFamily);
  const mode = modeKey ? t(modeKey) : titleCase(runMode);
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
  const t = useTranslations("projectDetail");
  const fmt = useFormatters();
  const runningCount = live?.runningCount ?? agent.runningCount;
  const isPaused = live?.isPaused ?? agent.runPaused;

  if (runningCount > 0) {
    return (
      <span className="inline-flex items-center gap-1.5">
        <StatusDot status="pending" />
        <Badge>{t("running", { count: runningCount })}</Badge>
      </span>
    );
  }
  if (isPaused) {
    return (
      <span className="inline-flex items-center gap-1.5">
        <StatusDot status="muted" />
        <Badge variant="outline">{t("paused")}</Badge>
      </span>
    );
  }
  if (live?.nextScheduledAt) {
    return (
      <span className="inline-flex items-center gap-1.5">
        <StatusDot status="warn" />
        <Badge variant="secondary" title={live.nextScheduledAt}>
          {t("next", { when: fmt.formatRelativeTime(live.nextScheduledAt) })}
        </Badge>
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1.5">
      <StatusDot status="muted" />
      <Badge variant="outline">{t("idle")}</Badge>
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
  const t = useTranslations("projectDetail");
  const statusLabel = useTranslations("agentActivity.statuses");
  const fmt = useFormatters();
  if (!status && !at) {
    return <span className="text-muted-foreground text-sm">{t("neverRun")}</span>;
  }
  const dot = AGENT_RUN_STATUS_DOT[(status ?? "").toLowerCase()] ?? "muted";
  return (
    <span className="inline-flex items-center gap-1.5">
      {status && (
        <>
          <StatusDot status={dot} />
          <Badge variant={dot === "error" ? "destructive" : "secondary"}>
            {statusLabel.has(status.toLowerCase())
              ? statusLabel(status.toLowerCase())
              : titleCase(status)}
          </Badge>
        </>
      )}
      {at && (
        <span className="text-muted-foreground text-xs" title={at}>
          {fmt.formatRelativeTime(at)}
        </span>
      )}
    </span>
  );
}
