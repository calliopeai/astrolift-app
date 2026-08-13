"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import {
  ActivityIcon,
  AlertTriangleIcon,
  BotIcon,
  BoxIcon,
  ChevronRightIcon,
  ExternalLinkIcon,
  FileBoxIcon,
  GitBranchIcon,
  PlusIcon,
  RocketIcon,
  Settings2Icon,
  ShieldIcon,
  Trash2Icon,
  UsersIcon,
  WorkflowIcon,
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import * as React from "react";
import { toast } from "sonner";

import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { EmptyState } from "@/components/EmptyState";
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
import { WorkflowTopology } from "@/components/workflows/workflow-topology";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { SOFT_DELETE_PROJECT } from "@/graphql/identity/identity.mutations";
import { LIST_MEMBERS, LIST_PROJECTS } from "@/graphql/identity/identity.queries";
import type {
  AstroliftMember,
  AstroliftProject,
  MutationResult,
} from "@/graphql/identity/identity.types";
import { LIST_AGENT_LIVE_STATUS, LIST_AGENT_WORKLOADS } from "@/graphql/agents/agents.queries";
import type {
  AstroliftAgentListItem,
  AstroliftAgentLiveStatus,
} from "@/graphql/agents/agents.types";
import { LIST_APPS } from "@/graphql/registry/registry.queries";
import type { AstroliftRegisteredApp, ProvisioningStatus } from "@/graphql/registry/registry.types";
import { useModules } from "@/graphql/user/user.hooks";
import {
  LIST_TIERED_WORKFLOW_DEFINITIONS,
  LIST_WORKFLOW_DEFINITION_RUNS,
} from "@/graphql/workflows/tiered.queries";
import type {
  WorkflowDefinitionRun,
  WorkflowDefinitionSummary,
} from "@/graphql/workflows/tiered.types";
import { formatRelativeAge } from "@/lib/format";
import { useFormatters } from "@/lib/i18n/formatters";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

const statusDot: Record<ProvisioningStatus, "ok" | "warn" | "error" | "pending"> = {
  ready: "ok",
  pending: "warn",
  provisioning: "pending",
  failed: "error",
};

interface ProjectsResp {
  astroliftProjects: AstroliftProject[];
}
interface AppsResp {
  astroliftApps: AstroliftRegisteredApp[];
}
interface MembersResp {
  astroliftMembers: AstroliftMember[];
}
interface AgentWorkloadsResp {
  agentWorkloads: AstroliftAgentListItem[];
}
interface AgentLiveStatusResp {
  agentLiveStatus: AstroliftAgentLiveStatus[];
}
interface ProjectWorkflowsResp {
  workflowDefinitions: WorkflowDefinitionSummary[];
}
interface ProjectWorkflowRunsResp {
  workflowDefinitionRuns: WorkflowDefinitionRun[];
}

export function ProjectDetailClient({ slug }: { slug: string }) {
  const router = useRouter();
  const fmt = useFormatters();
  const [settingsOpen, setSettingsOpen] = React.useState(false);
  const [confirmOpen, setConfirmOpen] = React.useState(false);
  const modules = useModules();
  const permissions = useMyPermissions();
  const canViewApps = modules.canView("apps");
  const canCreateApp = modules.canCreate("apps");
  const canViewAgents = modules.canView("agents");
  const canCreateAgent = modules.canCreate("agents");
  const canViewWorkflows = modules.canView("workflows");
  const canManageMembers = permissions.can("org.manage_members");

  const projects = useQuery<ProjectsResp>(LIST_PROJECTS);
  const apps = useQuery<AppsResp>(LIST_APPS, {
    skip: modules.loading || !canViewApps,
  });
  const members = useQuery<MembersResp>(LIST_MEMBERS, {
    skip: permissions.loading || !canManageMembers,
  });

  const project = projects.data?.astroliftProjects.find((p) => p.slug === slug);
  const orgId = project?.organization.id ?? "";
  const agents = useQuery<AgentWorkloadsResp>(LIST_AGENT_WORKLOADS, {
    variables: { orgId, projectSlug: slug },
    skip: !orgId || modules.loading || !canViewAgents,
    fetchPolicy: "cache-and-network",
  });
  const liveAgents = useQuery<AgentLiveStatusResp>(LIST_AGENT_LIVE_STATUS, {
    variables: { orgId, projectSlug: slug, workloadId: null },
    skip: !orgId || modules.loading || !canViewAgents,
    fetchPolicy: "cache-and-network",
    pollInterval: 15_000,
  });
  const workflows = useQuery<ProjectWorkflowsResp>(LIST_TIERED_WORKFLOW_DEFINITIONS, {
    variables: { orgId, projectId: project?.id ?? null },
    skip: !orgId || !project?.id || modules.loading || !canViewWorkflows,
    fetchPolicy: "cache-and-network",
  });
  const workflowRuns = useQuery<ProjectWorkflowRunsResp>(LIST_WORKFLOW_DEFINITION_RUNS, {
    variables: { orgId, projectId: project?.id ?? null, status: null, limit: 50 },
    skip: !orgId || !project?.id || modules.loading || !canViewWorkflows,
    fetchPolicy: "cache-and-network",
    pollInterval: 10_000,
  });

  const [softDelete, { loading: deleting }] = useMutation<{
    softDeleteProject: MutationResult<{ id: string; deleted: boolean }>;
  }>(SOFT_DELETE_PROJECT, {
    refetchQueries: [{ query: LIST_PROJECTS }],
    awaitRefetchQueries: true,
  });

  // Filter on the client. There's no singular astroliftProject(slug)
  // resolver yet, so we pull the list and pick — fine for the
  // typical org with O(10) projects. A focused project read will
  // matter for orgs with hundreds of projects (#TBD backend ticket).
  const projectApps = apps.data?.astroliftApps.filter((a) => a.projectSlug === slug) ?? [];
  const projectAgents = canViewAgents ? (agents.data?.agentWorkloads ?? []) : [];
  const projectWorkflows = canViewWorkflows ? (workflows.data?.workflowDefinitions ?? []) : [];
  const liveByWorkloadId = React.useMemo(() => {
    const map = new Map<string, AstroliftAgentLiveStatus>();
    for (const row of liveAgents.data?.agentLiveStatus ?? []) {
      map.set(row.workloadId, row);
    }
    return map;
  }, [liveAgents.data?.agentLiveStatus]);

  // Members of this project: scopeKind=PROJECT and scopeId matches the
  // project's id. Org-wide members also inherit access — surface those
  // in a separate group below the explicit project members so it's
  // clear which are direct vs inherited.
  const directMembers =
    members.data?.astroliftMembers.filter(
      (m) => m.scopeKind === "PROJECT" && m.scopeId === project?.id
    ) ?? [];

  if (projects.loading && !project) {
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

  async function handleDelete() {
    if (!project) return;
    const { data } = await softDelete({
      variables: { input: { id: project.id } },
    });
    if (data?.softDeleteProject.ok) {
      toast.success(`Deleted ${project.slug}`);
      router.push("/administration/projects");
    } else {
      throw new Error(data?.softDeleteProject.errors?.[0]?.message ?? "Delete failed");
    }
  }

  const activeApps = projectApps.filter((a) => a.provisioningStatus === "ready").length;
  const failingApps = projectApps.filter((a) => a.provisioningStatus === "failed").length;
  const hasAgents = projectAgents.length > 0;
  const hasApps = projectApps.length > 0;
  const hasWorkflows = projectWorkflows.length > 0;
  const agentQueryError = canViewAgents ? agents.error : undefined;
  const appsQueryError = canViewApps ? apps.error : undefined;
  const workloadQueryError = appsQueryError ?? agentQueryError;
  const agentOnlyProject = hasAgents && canViewApps && !hasApps && !apps.error;
  const mixedProject = hasAgents && hasApps;
  const workloadQueriesLoading =
    (canViewApps && apps.loading && !apps.data) ||
    modules.loading ||
    (canViewAgents && agents.loading && !agents.data) ||
    (canViewWorkflows && workflows.loading && !workflows.data);
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
    for (const run of workflowRuns.data?.workflowDefinitionRuns ?? []) {
      if (!latest.has(run.definitionGuid)) latest.set(run.definitionGuid, run);
    }
    return latest;
  })();
  const activeWorkflowRuns = (workflowRuns.data?.workflowDefinitionRuns ?? []).filter(
    (run) => run.status === "running"
  ).length;
  const workflowsNeedingAttention = [...latestWorkflowRunByDefinition.values()].filter((run) =>
    ["failed", "timed_out"].includes(run.status)
  ).length;

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
            />
          )}
          {mixedProject && (
            <StatTile
              icon={RocketIcon}
              label="Registered apps"
              value={projectApps.length}
              footer={failingApps > 0 ? `${failingApps} failing` : `${activeApps} ready`}
              className={failingApps > 0 ? "border-warning-border bg-warning/5" : undefined}
            />
          )}
          <StatTile
            icon={ActivityIcon}
            label="Active agent runs"
            value={runningAgentRuns}
            footer={runningAgentRuns === 1 ? "run in progress" : "runs in progress"}
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
          />
        </div>
      ) : hasApps ? (
        <div className="grid gap-4 sm:grid-cols-3">
          <StatTile
            icon={RocketIcon}
            label="Registered apps"
            value={projectApps.length}
            footer={failingApps > 0 ? `${failingApps} failing` : `${activeApps} ready`}
            className={failingApps > 0 ? "border-warning-border bg-warning/5" : undefined}
          />
          <StatTile
            icon={BoxIcon}
            label="Active deployments"
            value={activeApps}
            footer="apps with a healthy latest rollout"
          />
          {canManageMembers && (
            <StatTile
              icon={UsersIcon}
              label="Direct members"
              value={directMembers.length}
              footer="users granted project-scope access"
            />
          )}
        </div>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2">
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
          />
          {canManageMembers && (
            <StatTile
              icon={UsersIcon}
              label="Direct members"
              value={directMembers.length}
              footer="users granted project-scope access"
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

      {canViewWorkflows && workflows.error && (
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
        <Card>
          <CardHeader className="flex flex-row items-start justify-between gap-3 space-y-0">
            <div>
              <CardTitle className="flex items-center gap-2 text-base">
                <WorkflowIcon className="size-4" /> Workflows in this project
              </CardTitle>
              <CardDescription>
                Repository-declared pipelines and their ordered agent, environment, model, and
                output bindings.
              </CardDescription>
            </div>
            <Button asChild size="sm" variant="outline">
              <Link href="/workflows">View all</Link>
            </Button>
          </CardHeader>
          <CardContent className="space-y-4">
            {projectWorkflows.map((workflow) => (
              <section key={workflow.guid} className="rounded-lg border p-4">
                <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <div className="flex items-center gap-2">
                      <h3 className="font-semibold">{workflow.name}</h3>
                      <Badge variant="outline" className="capitalize">
                        {workflow.patternKind.replace(/_/g, " ")}
                      </Badge>
                      {latestWorkflowRunByDefinition.get(workflow.guid) && (
                        <Badge
                          variant={
                            ["failed", "timed_out"].includes(
                              latestWorkflowRunByDefinition.get(workflow.guid)?.status ?? ""
                            )
                              ? "destructive"
                              : "secondary"
                          }
                          className="capitalize"
                        >
                          {latestWorkflowRunByDefinition
                            .get(workflow.guid)
                            ?.status.replace(/_/g, " ")}
                        </Badge>
                      )}
                    </div>
                    <p className="text-muted-foreground mt-1 font-mono text-xs">
                      {workflow.sourcePath || workflow.slug}
                    </p>
                  </div>
                  <Button asChild size="sm" variant="ghost">
                    <Link href={`/workflows/${encodeURIComponent(workflow.slug)}/builder`}>
                      Open <ExternalLinkIcon className="size-3" />
                    </Link>
                  </Button>
                </div>
                <WorkflowTopology stages={workflow.stages} />
              </section>
            ))}
          </CardContent>
        </Card>
      )}

      {hasAgents && (
        <Card>
          <CardHeader className="flex flex-row items-start justify-between gap-3 space-y-0">
            <div>
              <CardTitle className="flex items-center gap-2 text-base">
                <BotIcon className="size-4" /> Agents in this project
              </CardTitle>
              <CardDescription>
                Registered agent workloads for {project.team.slug}/{project.slug}. Live status
                refreshes automatically.
              </CardDescription>
            </div>
            {canCreateAgent && (
              <Button asChild size="sm" variant="outline">
                <Link href="/agents/new">
                  <PlusIcon className="size-4" /> Register repo
                </Link>
              </Button>
            )}
          </CardHeader>
          <CardContent className="p-0">
            <Table aria-label="Agents in this project">
              <TableHeader>
                <TableRow>
                  <TableHead>Agent</TableHead>
                  <TableHead>Used by</TableHead>
                  <TableHead>Source</TableHead>
                  <TableHead>Run mode</TableHead>
                  <TableHead>Live status</TableHead>
                  <TableHead>Last run</TableHead>
                  <TableHead className="text-right"></TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {projectAgents.map((agent) => (
                  <TableRow key={agent.id}>
                    <TableCell>
                      <Link
                        href={`/agents/${encodeURIComponent(agent.slug)}/build`}
                        className="font-medium hover:underline"
                      >
                        {agent.name}
                      </Link>
                      <div className="text-muted-foreground font-mono text-xs">{agent.slug}</div>
                    </TableCell>
                    <TableCell>
                      {(workflowMembershipByAgent.get(agent.slug) ?? []).length > 0 ? (
                        <div className="flex max-w-64 flex-wrap gap-1">
                          {(workflowMembershipByAgent.get(agent.slug) ?? []).map((workflow) => (
                            <Badge key={workflow.guid} variant="secondary" asChild>
                              <Link
                                href={`/workflows/${encodeURIComponent(workflow.slug)}/builder`}
                              >
                                {workflow.slug}
                              </Link>
                            </Badge>
                          ))}
                        </div>
                      ) : (
                        <Badge variant="outline">Standalone</Badge>
                      )}
                    </TableCell>
                    <TableCell>
                      {agent.sourceRepo ? (
                        agent.sourceUrl ? (
                          <a
                            href={agent.sourceUrl}
                            target="_blank"
                            rel="noreferrer"
                            className="text-muted-foreground hover:text-foreground inline-flex items-center gap-1 text-sm"
                          >
                            <GitBranchIcon className="size-3.5" />
                            {agent.sourceRepo}
                          </a>
                        ) : (
                          <span className="text-muted-foreground inline-flex items-center gap-1 text-sm">
                            <GitBranchIcon className="size-3.5" />
                            {agent.sourceRepo}
                          </span>
                        )
                      ) : (
                        <span className="text-muted-foreground">—</span>
                      )}
                    </TableCell>
                    <TableCell>
                      <Badge variant="outline">
                        {formatAgentRunMode(agent.runFamily, agent.runMode)}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      <AgentLiveStatus agent={agent} live={liveByWorkloadId.get(agent.id)} />
                    </TableCell>
                    <TableCell>
                      <AgentLastRun
                        status={
                          liveByWorkloadId.get(agent.id)?.lastRunStatus ?? agent.lastRunStatus
                        }
                        at={liveByWorkloadId.get(agent.id)?.lastRunAt ?? agent.lastRunAt}
                      />
                    </TableCell>
                    <TableCell className="text-right">
                      <Button asChild size="sm" variant="ghost">
                        <Link href={`/agents/${encodeURIComponent(agent.slug)}/build`}>
                          Open <ExternalLinkIcon className="size-3" />
                        </Link>
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
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
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <RocketIcon className="size-4" /> Apps in this project
            </CardTitle>
            <CardDescription>
              Registered apps associated with {project.team.slug}/{project.slug}. Click a row to
              open the app detail page.
            </CardDescription>
          </CardHeader>
          <CardContent className="p-0">
            {apps.loading && projectApps.length === 0 ? (
              <div className="space-y-2 p-6">
                <Skeleton className="h-12 w-full" />
                <Skeleton className="h-12 w-full" />
              </div>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead></TableHead>
                    <TableHead>App</TableHead>
                    <TableHead>Source</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Created</TableHead>
                    <TableHead className="text-right"></TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {projectApps.map((a) => (
                    <TableRow key={a.id}>
                      <TableCell className="w-8">
                        <StatusDot status={statusDot[a.provisioningStatus]} />
                      </TableCell>
                      <TableCell>
                        <Link href={`/apps/${a.slug}`} className="hover:underline">
                          <div className="font-medium">{a.name}</div>
                          <div className="text-muted-foreground font-mono text-xs">{a.slug}</div>
                        </Link>
                      </TableCell>
                      <TableCell className="text-sm">
                        {a.sourceRepo ? (
                          <span className="font-mono text-xs">{a.sourceRepo}</span>
                        ) : (
                          <span className="text-muted-foreground">—</span>
                        )}
                      </TableCell>
                      <TableCell>
                        <Badge variant="secondary" className="capitalize">
                          {a.provisioningStatus}
                        </Badge>
                      </TableCell>
                      <TableCell className="text-muted-foreground text-sm">
                        {fmt.formatDate(a.createdAt)}
                      </TableCell>
                      <TableCell className="text-right">
                        <Button asChild size="sm" variant="ghost">
                          <Link href={`/apps/${a.slug}`}>
                            Open <ExternalLinkIcon className="size-3" />
                          </Link>
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>
      )}

      {/* ─── members ───────────────────────────────────────────────────── */}
      {canManageMembers && (
        <Card>
          <CardHeader className="flex flex-row items-start justify-between gap-3 space-y-0">
            <div>
              <CardTitle className="flex items-center gap-2 text-base">
                <ShieldIcon className="size-4" /> Project members
              </CardTitle>
              <CardDescription>
                Members with explicit access granted at the project scope. Org-wide and team-wide
                members are not listed here.
              </CardDescription>
            </div>
            <Can permission="org.manage_members">
              <Button asChild size="sm">
                <Link href="/administration/members">
                  <PlusIcon className="size-4" /> Invite
                </Link>
              </Button>
            </Can>
          </CardHeader>
          <CardContent className="p-0">
            {members.loading && directMembers.length === 0 ? (
              <div className="space-y-2 p-6">
                <Skeleton className="h-10 w-full" />
                <Skeleton className="h-10 w-full" />
              </div>
            ) : directMembers.length === 0 ? (
              <div className="p-6">
                <EmptyState
                  icon={<UsersIcon className="size-5" />}
                  title="No direct project members"
                  description="Anyone with team or org-wide access already sees this project. Grant explicit project-scope access from the Members page."
                  actionHref="/administration/members"
                  actionLabel="Manage members"
                />
              </div>
            ) : (
              <ul className="divide-y">
                {directMembers.map((m) => (
                  <li key={m.id} className="flex items-center justify-between px-6 py-3">
                    <div>
                      <div className="font-medium">{m.user.username || m.user.email}</div>
                      <div className="text-muted-foreground text-xs">
                        {m.user.email} · joined {m.joinedAt ? fmt.formatDate(m.joinedAt) : "—"}
                      </div>
                    </div>
                    <Badge variant="outline" className="text-xs uppercase">
                      {m.scopeKind}
                    </Badge>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
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
        onConfirm={handleDelete}
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
