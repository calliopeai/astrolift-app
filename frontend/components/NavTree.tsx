"use client";

import { useQuery } from "@apollo/client/react";
import {
  AlertCircleIcon,
  BoltIcon,
  BotIcon,
  BoxIcon,
  Building2Icon,
  ChevronRightIcon,
  CircleDashedIcon,
  FileBoxIcon,
  ListChecksIcon,
  Loader2Icon,
  RocketIcon,
  TimerIcon,
  UsersIcon,
  WorkflowIcon,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import * as React from "react";

import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import {
  SidebarGroup,
  SidebarGroupLabel,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarMenuSub,
  SidebarMenuSubButton,
  SidebarMenuSubItem,
} from "@/components/ui/sidebar";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";
import { useModules } from "@/graphql/user/user.hooks";
import { LIST_NAV_TREE } from "@/graphql/identity/identity.queries";
import type {
  AstroliftAppStatus,
  AstroliftAppSummary,
  AstroliftNavTree,
  AstroliftNavTreeProjectNode,
  AstroliftNavTreeTeamNode,
  AstroliftNavTreeWorkflow,
} from "@/graphql/identity/identity.types";

interface NavTreeResp {
  astroliftNavTree: AstroliftNavTree | null;
}

const OPEN_KEY = "astrolift.nav.tree.open.v1";

// Map provisioning_status to a small dot colour. Kept in step with
// the status badge palette in app-detail-client.tsx so the sidebar
// and detail page agree on what "failed" looks like at a glance.
const STATUS_DOT_CLASS: Record<AstroliftAppStatus, string> = {
  ready: "bg-success",
  provisioning: "bg-warning",
  pending: "bg-slate-400",
  failed: "bg-danger",
};

function statusIcon(status: AstroliftAppStatus) {
  if (status === "provisioning") {
    return <Loader2Icon className="text-warning-fg size-3 shrink-0 animate-spin" aria-hidden />;
  }
  if (status === "failed") {
    return <AlertCircleIcon className="text-danger-fg size-3 shrink-0" aria-hidden />;
  }
  return (
    <span
      aria-hidden
      className={cn(
        "inline-block size-1.5 shrink-0 rounded-full",
        STATUS_DOT_CLASS[status] ?? "bg-slate-400"
      )}
    />
  );
}

function loadOpenState(): Record<string, boolean> {
  if (typeof window === "undefined") return {};
  try {
    const raw = window.localStorage.getItem(OPEN_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    return typeof parsed === "object" && parsed !== null ? parsed : {};
  } catch {
    return {};
  }
}

function saveOpenState(state: Record<string, boolean>) {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(OPEN_KEY, JSON.stringify(state));
  } catch {
    // localStorage might be disabled (private mode, quota) -- degrade silently
  }
}

/** Stable key for a tree node so its open/closed state survives refreshes. */
function nodeKey(kind: "org" | "team" | "project" | "workflow", id: string) {
  return `${kind}:${id}`;
}

interface WorkflowGraph {
  byId: Map<string, AstroliftNavTreeWorkflow>;
  roots: AstroliftNavTreeWorkflow[];
}

function buildWorkflowGraph(workflows: AstroliftNavTreeWorkflow[]): WorkflowGraph {
  const byId = new Map(workflows.map((workflow) => [workflow.id, workflow]));
  const referenced = new Set(
    workflows.flatMap((workflow) =>
      workflow.childWorkflowIds.filter((childId) => byId.has(childId))
    )
  );
  const roots = workflows.filter((workflow) => !referenced.has(workflow.id));
  return { byId, roots: roots.length > 0 ? roots : workflows };
}

function findWorkflowTrail(
  workflows: AstroliftNavTreeWorkflow[],
  activeWorkflowSlug: string | null,
  activeAgentSlug: string | null
): string[] | null {
  const graph = buildWorkflowGraph(workflows);

  const visit = (workflow: AstroliftNavTreeWorkflow, ancestry: Set<string>): string[] | null => {
    if (ancestry.has(workflow.id)) return null;
    const nextAncestry = new Set(ancestry).add(workflow.id);
    if (
      workflow.slug === activeWorkflowSlug ||
      workflow.agents.some((agent) => agent.primitiveSlug === activeAgentSlug)
    ) {
      return [workflow.id];
    }
    for (const childId of workflow.childWorkflowIds) {
      const child = graph.byId.get(childId);
      if (!child) continue;
      const childTrail = visit(child, nextAncestry);
      if (childTrail) return [workflow.id, ...childTrail];
    }
    return null;
  };

  for (const root of graph.roots) {
    const trail = visit(root, new Set());
    if (trail) return trail;
  }
  return null;
}

interface NavTreeSkeletonProps {
  rows?: number;
}

function NavTreeSkeleton({ rows = 3 }: NavTreeSkeletonProps) {
  return (
    <SidebarGroup className="group-data-[collapsible=icon]:hidden">
      <SidebarGroupLabel>Workspace</SidebarGroupLabel>
      <SidebarMenu>
        <SidebarMenuItem>
          <SidebarMenuButton disabled>
            <Skeleton className="size-4 rounded" />
            <Skeleton className="h-3 w-24" />
          </SidebarMenuButton>
          <SidebarMenuSub>
            {Array.from({ length: rows }).map((_, i) => (
              <SidebarMenuSubItem key={i}>
                <div className="flex h-7 items-center gap-2 px-2">
                  <Skeleton className="size-1.5 rounded-full" />
                  <Skeleton className="h-3 w-28" />
                </div>
              </SidebarMenuSubItem>
            ))}
          </SidebarMenuSub>
        </SidebarMenuItem>
      </SidebarMenu>
    </SidebarGroup>
  );
}

/**
 * Collapsible Org -> Team -> Project -> App tree.
 *
 * Renders as a single `SidebarGroup` so it sits flush above the flat
 * `AstroliftNav` (platform sections). Each level keeps its own
 * collapsed/expanded state in localStorage; on first load the group
 * containing the active route (`/apps/<slug>`) opens automatically so
 * operators land directly on the leaf they were last looking at.
 *
 * Empty states surface inline rather than hiding the level entirely --
 * an org with no teams shows "No teams yet" with a CTA, so first-time
 * operators have a clear next action without leaving the sidebar.
 *
 * Polled with `cache-and-network` so the tree updates without a full
 * reload as the user creates teams, projects, or registers apps.
 */
export function NavTree() {
  const pathname = usePathname();
  const { canView, loading: modulesLoading } = useModules();
  const permissions = useMyPermissions();
  const canViewApps = canView("apps");
  const canViewAgents = canView("agents");
  const canViewWorkflows = canView("workflows");
  const canViewResources = permissions.can("project.read");
  const { data, loading, error } = useQuery<NavTreeResp>(LIST_NAV_TREE, {
    fetchPolicy: "cache-and-network",
  });

  // Active app inferred from the URL. `/apps/<slug>` and any of its
  // children (`/apps/<slug>/workloads/...`) light up the same leaf.
  const activeAppSlug = React.useMemo(() => {
    const match = pathname.match(/^\/apps\/([^/]+)/);
    return match?.[1] ?? null;
  }, [pathname]);
  const activeAgentSlug = React.useMemo(() => {
    const match = pathname.match(/^\/agents\/([^/]+)/);
    return match?.[1] ?? null;
  }, [pathname]);
  const activeWorkflowSlug = React.useMemo(() => {
    const match = pathname.match(/^\/workflows\/([^/]+)/);
    return match?.[1] ?? null;
  }, [pathname]);
  const activeProjectSlug = React.useMemo(() => {
    const match = pathname.match(/^\/projects\/([^/]+)/);
    return match?.[1] ?? null;
  }, [pathname]);
  const activeResourceProjectSlug = React.useMemo(() => {
    const match = pathname.match(/^\/projects\/([^/]+)\/resources(?:\/|$)/);
    return match?.[1] ?? null;
  }, [pathname]);

  const [open, setOpen] = React.useState<Record<string, boolean>>({});
  React.useEffect(() => {
    setOpen(loadOpenState());
  }, []);

  const tree = data?.astroliftNavTree ?? null;

  // First time the operator loads a page under `/apps/<slug>`, open
  // the team + project that contain that slug so the leaf is visible
  // without manual digging. Only flips keys that are still undefined
  // in state so the user's explicit collapses aren't reverted.
  React.useEffect(() => {
    if (
      !tree ||
      (!activeAppSlug && !activeAgentSlug && !activeWorkflowSlug && !activeProjectSlug)
    ) {
      return;
    }
    setOpen((prev) => {
      const next = { ...prev };
      let dirty = false;
      const ensure = (key: string) => {
        if (next[key] === undefined) {
          next[key] = true;
          dirty = true;
        }
      };
      ensure(nodeKey("org", tree.organization.id));
      for (const teamNode of tree.teams) {
        for (const projectNode of teamNode.projects) {
          const workflowTrail = findWorkflowTrail(
            projectNode.workflows,
            activeWorkflowSlug,
            activeAgentSlug
          );
          const hit =
            projectNode.project.slug === activeProjectSlug ||
            projectNode.apps.some((a) => a.slug === activeAppSlug) ||
            projectNode.standaloneAgents.some((a) => a.primitiveSlug === activeAgentSlug) ||
            workflowTrail !== null;
          if (hit) {
            ensure(nodeKey("team", teamNode.team.id));
            ensure(nodeKey("project", projectNode.project.id));
            const workflowPath: string[] = [];
            for (const workflowId of workflowTrail ?? []) {
              workflowPath.push(workflowId);
              ensure(nodeKey("workflow", workflowPath.join(":")));
            }
          }
        }
        if (teamNode.unassignedApps.some((a) => a.slug === activeAppSlug)) {
          ensure(nodeKey("team", teamNode.team.id));
        }
      }
      if (dirty) saveOpenState(next);
      return dirty ? next : prev;
    });
  }, [tree, activeAppSlug, activeAgentSlug, activeWorkflowSlug, activeProjectSlug]);

  function toggle(key: string, defaultOpen: boolean) {
    setOpen((prev) => {
      const current = prev[key] ?? defaultOpen;
      const next = { ...prev, [key]: !current };
      saveOpenState(next);
      return next;
    });
  }

  // Apps module gate (spec 36 §1.3): this Org -> Team -> Project -> App
  // tree exists solely to navigate to apps, so it inherits the Apps
  // module's server-authoritative visibility. While `me.modules` loads we
  // keep rendering to avoid a sidebar shrink-and-grow on refresh — matching
  // AstroliftNav.
  if (!modulesLoading && !canViewApps && !canViewAgents && !canViewWorkflows) {
    return null;
  }

  if (loading && !data) {
    return <NavTreeSkeleton />;
  }

  if (error || !tree) {
    // The query is self-service (tenant-scoped, no permission gate),
    // so an error here means a transport/auth problem. Don't render a
    // misleading empty tree -- skip the section and let the flat nav
    // carry the operator forward.
    return null;
  }

  const orgKey = nodeKey("org", tree.organization.id);
  const orgOpen = open[orgKey] ?? true;
  const totalApps =
    tree.teams.reduce(
      (sum, t) =>
        sum +
        t.unassignedApps.length +
        t.projects.reduce(
          (s, p) =>
            s +
            p.apps.length +
            p.standaloneAgents.length +
            p.workflows.reduce((count, workflow) => count + workflow.agents.length, 0),
          0
        ),
      0
    ) + tree.unassignedApps.length;

  return (
    <SidebarGroup className="group-data-[collapsible=icon]:hidden">
      <SidebarGroupLabel>Workspace</SidebarGroupLabel>
      <SidebarMenu>
        <Collapsible open={orgOpen} onOpenChange={() => toggle(orgKey, true)} asChild>
          <SidebarMenuItem>
            <SidebarMenuButton asChild tooltip={tree.organization.name} className="group/org">
              <div className="flex w-full items-center">
                <Link
                  href="/administration/organization"
                  className="flex min-w-0 flex-1 items-center gap-2"
                  title={tree.organization.name}
                >
                  <Building2Icon className="shrink-0" />
                  <span className="font-medium break-words">{tree.organization.name}</span>
                </Link>
                <CollapsibleTrigger
                  className="hover:bg-sidebar-accent -mr-1 ml-auto flex size-5 shrink-0 items-center justify-center rounded-sm"
                  aria-label={`Toggle ${tree.organization.name}`}
                >
                  <ChevronRightIcon
                    className={cn("size-3.5 transition-transform", orgOpen && "rotate-90")}
                  />
                </CollapsibleTrigger>
              </div>
            </SidebarMenuButton>
            <CollapsibleContent>
              <SidebarMenuSub className="mx-2 px-1.5">
                {tree.teams.length === 0 ? (
                  <EmptyRow
                    href="/administration/teams"
                    label="No teams yet"
                    cta="Create your first team"
                  />
                ) : (
                  tree.teams.map((teamNode) => (
                    <TeamNode
                      key={teamNode.team.id}
                      node={teamNode}
                      activeAppSlug={activeAppSlug}
                      activeAgentSlug={activeAgentSlug}
                      activeWorkflowSlug={activeWorkflowSlug}
                      activeResourceProjectSlug={activeResourceProjectSlug}
                      canViewApps={canViewApps}
                      canViewAgents={canViewAgents}
                      canViewWorkflows={canViewWorkflows}
                      canViewResources={canViewResources}
                      open={open}
                      toggle={toggle}
                    />
                  ))
                )}
                {canViewApps && tree.unassignedApps.length > 0 ? (
                  <UnassignedAppsBlock
                    label="Unassigned apps"
                    apps={tree.unassignedApps}
                    activeAppSlug={activeAppSlug}
                  />
                ) : null}
                {totalApps === 0 && tree.teams.length > 0 ? (
                  <EmptyRow href="/apps/new" label="No apps yet" cta="Register first app" />
                ) : null}
              </SidebarMenuSub>
            </CollapsibleContent>
          </SidebarMenuItem>
        </Collapsible>
      </SidebarMenu>
    </SidebarGroup>
  );
}

interface TeamNodeProps {
  node: AstroliftNavTreeTeamNode;
  activeAppSlug: string | null;
  activeAgentSlug: string | null;
  activeWorkflowSlug: string | null;
  activeResourceProjectSlug: string | null;
  canViewApps: boolean;
  canViewAgents: boolean;
  canViewWorkflows: boolean;
  canViewResources: boolean;
  open: Record<string, boolean>;
  toggle: (key: string, defaultOpen: boolean) => void;
}

function TeamNode({
  node,
  activeAppSlug,
  activeAgentSlug,
  activeWorkflowSlug,
  activeResourceProjectSlug,
  canViewApps,
  canViewAgents,
  canViewWorkflows,
  canViewResources,
  open,
  toggle,
}: TeamNodeProps) {
  const key = nodeKey("team", node.team.id);
  const isOpen = open[key] ?? true;
  const hasChildren = node.projects.length > 0 || node.unassignedApps.length > 0;

  return (
    <Collapsible open={isOpen} onOpenChange={() => toggle(key, true)} asChild>
      <SidebarMenuSubItem>
        <SidebarMenuSubButton
          asChild
          className="group/team !flex !h-auto !min-h-7 !w-full !max-w-none !overflow-visible py-1 !whitespace-normal [&>span:last-child]:!overflow-visible [&>span:last-child]:!text-clip [&>span:last-child]:!whitespace-normal"
        >
          <div className="flex w-full min-w-0 items-start gap-2">
            <UsersIcon className="text-sidebar-foreground/70 mt-0.5 shrink-0" />
            <Link
              href={`/administration/teams?team=${encodeURIComponent(node.team.slug)}`}
              className="min-w-0 flex-1 text-left leading-tight break-words whitespace-normal"
              title={node.team.name}
            >
              {node.team.name}
            </Link>
            <CollapsibleTrigger
              className="hover:bg-sidebar-accent -mr-1 flex size-5 shrink-0 translate-x-3.5 items-center justify-center rounded-sm"
              aria-label={`Toggle ${node.team.name}`}
            >
              <ChevronRightIcon
                className={cn("size-3 transition-transform", isOpen && "rotate-90")}
              />
            </CollapsibleTrigger>
          </div>
        </SidebarMenuSubButton>
        <CollapsibleContent>
          <SidebarMenuSub className="mx-2 px-1.5">
            {hasChildren ? (
              <>
                {node.projects.map((projectNode) => (
                  <ProjectNode
                    key={projectNode.project.id}
                    node={projectNode}
                    activeAppSlug={activeAppSlug}
                    activeAgentSlug={activeAgentSlug}
                    activeWorkflowSlug={activeWorkflowSlug}
                    activeResourceProjectSlug={activeResourceProjectSlug}
                    canViewApps={canViewApps}
                    canViewAgents={canViewAgents}
                    canViewWorkflows={canViewWorkflows}
                    canViewResources={canViewResources}
                    open={open}
                    toggle={toggle}
                  />
                ))}
                {canViewApps && node.unassignedApps.length > 0 ? (
                  <UnassignedAppsBlock
                    label="Direct apps"
                    apps={node.unassignedApps}
                    activeAppSlug={activeAppSlug}
                  />
                ) : null}
                {/* #717 — Inline "create project" affordance so operators
                    don't have to navigate to /projects to add one. Mirrors
                    the EmptyRow pattern used when the team has no
                    projects yet; here it sits as a footer under the
                    existing project list. */}
                <AddProjectRow teamSlug={node.team.slug} />
              </>
            ) : (
              <EmptyRow
                href={`/teams/${node.team.slug}`}
                label="No projects yet"
                cta="Create a project"
              />
            )}
          </SidebarMenuSub>
        </CollapsibleContent>
      </SidebarMenuSubItem>
    </Collapsible>
  );
}

function AddProjectRow({ teamSlug }: { teamSlug: string }) {
  return (
    <SidebarMenuSubItem>
      <SidebarMenuSubButton
        asChild
        className="text-sidebar-foreground/60 hover:text-sidebar-foreground"
      >
        <Link
          href={`/administration/projects?team=${encodeURIComponent(teamSlug)}&new=1`}
          className="flex items-center gap-2 py-1 text-xs"
        >
          <span aria-hidden className="text-sm leading-none">
            +
          </span>
          <span>Add project</span>
        </Link>
      </SidebarMenuSubButton>
    </SidebarMenuSubItem>
  );
}

interface ProjectNodeProps {
  node: AstroliftNavTreeProjectNode;
  activeAppSlug: string | null;
  activeAgentSlug: string | null;
  activeWorkflowSlug: string | null;
  activeResourceProjectSlug: string | null;
  canViewApps: boolean;
  canViewAgents: boolean;
  canViewWorkflows: boolean;
  canViewResources: boolean;
  open: Record<string, boolean>;
  toggle: (key: string, defaultOpen: boolean) => void;
}

function ProjectNode({
  node,
  activeAppSlug,
  activeAgentSlug,
  activeWorkflowSlug,
  activeResourceProjectSlug,
  canViewApps,
  canViewAgents,
  canViewWorkflows,
  canViewResources,
  open,
  toggle,
}: ProjectNodeProps) {
  const key = nodeKey("project", node.project.id);
  const isOpen = open[key] ?? true;
  const visibleApps = canViewApps ? node.apps : [];
  const workflowGraph = React.useMemo(
    () => buildWorkflowGraph(canViewWorkflows ? node.workflows : []),
    [canViewWorkflows, node.workflows]
  );
  const visibleWorkflows = workflowGraph.roots;
  const visibleStandaloneAgents = canViewAgents ? node.standaloneAgents : [];

  return (
    <Collapsible open={isOpen} onOpenChange={() => toggle(key, true)} asChild>
      <SidebarMenuSubItem>
        <SidebarMenuSubButton
          asChild
          className="group/project !flex !h-auto !min-h-7 !w-full !max-w-none !overflow-visible py-1 !whitespace-normal [&>span:last-child]:!overflow-visible [&>span:last-child]:!text-clip [&>span:last-child]:!whitespace-normal"
        >
          <div className="flex w-full min-w-0 items-start gap-2">
            <FileBoxIcon className="text-sidebar-foreground/70 mt-0.5 shrink-0" />
            <Link
              href={`/projects/${encodeURIComponent(node.project.slug)}`}
              className="min-w-0 flex-1 text-left leading-tight break-words whitespace-normal"
              title={node.project.name}
            >
              {node.project.name}
            </Link>
            <CollapsibleTrigger
              className="hover:bg-sidebar-accent -mr-1 flex size-5 shrink-0 translate-x-7 items-center justify-center rounded-sm"
              aria-label={`Toggle ${node.project.name}`}
            >
              <ChevronRightIcon
                className={cn("size-3 transition-transform", isOpen && "rotate-90")}
              />
            </CollapsibleTrigger>
          </div>
        </SidebarMenuSubButton>
        <CollapsibleContent>
          <SidebarMenuSub className="mx-2 px-1.5">
            {visibleApps.length === 0 &&
            visibleWorkflows.length === 0 &&
            visibleStandaloneAgents.length === 0 ? (
              <EmptyRow href="/agents/new" label="No workloads yet" cta="Register a repo" />
            ) : (
              <>
                {visibleWorkflows.length > 0 && (
                  <>
                    <ProjectSectionLabel icon={WorkflowIcon} label="Workflows" />
                    {visibleWorkflows.map((workflow) => (
                      <WorkflowNode
                        key={workflow.id}
                        workflow={workflow}
                        activeAgentSlug={activeAgentSlug}
                        activeWorkflowSlug={activeWorkflowSlug}
                        showAgents={canViewAgents}
                        workflowGraph={workflowGraph}
                        open={open}
                        toggle={toggle}
                        ancestry={new Set()}
                        path={[]}
                      />
                    ))}
                  </>
                )}
                {visibleStandaloneAgents.length > 0 && (
                  <>
                    <ProjectSectionLabel
                      icon={BotIcon}
                      label={visibleWorkflows.length > 0 ? "Standalone agents" : "Agents"}
                    />
                    {visibleStandaloneAgents.map((agent) => (
                      <AppLeaf
                        key={agent.id}
                        app={agent}
                        active={activeAgentSlug === agent.primitiveSlug}
                      />
                    ))}
                  </>
                )}
                {visibleApps.length > 0 && (
                  <>
                    <ProjectSectionLabel icon={RocketIcon} label="Apps" />
                    {visibleApps.map((app) => (
                      <AppLeaf key={app.id} app={app} active={activeAppSlug === app.slug} />
                    ))}
                  </>
                )}
              </>
            )}
            {canViewResources && (
              <SidebarMenuSubItem>
                <SidebarMenuSubButton
                  asChild
                  isActive={activeResourceProjectSlug === node.project.slug}
                >
                  <Link
                    href={`/projects/${encodeURIComponent(node.project.slug)}/resources`}
                    className="text-sidebar-foreground/70 flex items-center gap-2"
                  >
                    <BoxIcon className="size-3.5" />
                    <span>Resources</span>
                  </Link>
                </SidebarMenuSubButton>
              </SidebarMenuSubItem>
            )}
          </SidebarMenuSub>
        </CollapsibleContent>
      </SidebarMenuSubItem>
    </Collapsible>
  );
}

function ProjectSectionLabel({ icon: Icon, label }: { icon: typeof WorkflowIcon; label: string }) {
  return (
    <SidebarMenuSubItem>
      <div className="text-2xs text-sidebar-foreground/50 flex items-center gap-1.5 px-2 pt-2 pb-0.5 font-semibold tracking-wide uppercase">
        <Icon className="size-3" aria-hidden />
        <span>{label}</span>
      </div>
    </SidebarMenuSubItem>
  );
}

function WorkflowNode({
  workflow,
  activeAgentSlug,
  activeWorkflowSlug,
  showAgents,
  workflowGraph,
  open,
  toggle,
  ancestry,
  path,
}: {
  workflow: AstroliftNavTreeWorkflow;
  activeAgentSlug: string | null;
  activeWorkflowSlug: string | null;
  showAgents: boolean;
  workflowGraph: WorkflowGraph;
  open: Record<string, boolean>;
  toggle: (key: string, defaultOpen: boolean) => void;
  ancestry: Set<string>;
  path: string[];
}) {
  const nextPath = [...path, workflow.id];
  const key = nodeKey("workflow", nextPath.join(":"));
  const isOpen = open[key] ?? true;
  const nextAncestry = new Set(ancestry).add(workflow.id);
  const childWorkflows = workflow.childWorkflowIds
    .map((childId) => workflowGraph.byId.get(childId))
    .filter(
      (child): child is AstroliftNavTreeWorkflow =>
        child !== undefined && !nextAncestry.has(child.id)
    );
  const hasChildren = childWorkflows.length > 0 || (showAgents && workflow.agents.length > 0);
  return (
    <Collapsible open={isOpen} onOpenChange={() => toggle(key, true)} asChild>
      <SidebarMenuSubItem>
        <SidebarMenuSubButton asChild isActive={activeWorkflowSlug === workflow.slug}>
          <div className="flex w-full min-w-0 items-center gap-2">
            <Link
              href={`/workflows/${encodeURIComponent(workflow.slug)}/observe`}
              className="flex min-w-0 flex-1 items-center gap-2"
            >
              <span
                className={cn(
                  "size-1.5 shrink-0 rounded-full",
                  workflow.isEnabled ? "bg-success" : "bg-slate-400"
                )}
              />
              <span className="truncate">{workflow.name}</span>
            </Link>
            {hasChildren && (
              <CollapsibleTrigger
                className="hover:bg-sidebar-accent ml-auto flex size-5 shrink-0 items-center justify-center rounded-sm"
                aria-label={`Toggle ${workflow.name}`}
              >
                <ChevronRightIcon
                  className={cn("size-3 transition-transform", isOpen && "rotate-90")}
                />
              </CollapsibleTrigger>
            )}
          </div>
        </SidebarMenuSubButton>
        {hasChildren && (
          <CollapsibleContent>
            <SidebarMenuSub className="mr-0 ml-3 px-1">
              {childWorkflows.map((child) => (
                <WorkflowNode
                  key={`${workflow.id}:${child.id}`}
                  workflow={child}
                  activeAgentSlug={activeAgentSlug}
                  activeWorkflowSlug={activeWorkflowSlug}
                  showAgents={showAgents}
                  workflowGraph={workflowGraph}
                  open={open}
                  toggle={toggle}
                  ancestry={nextAncestry}
                  path={nextPath}
                />
              ))}
              {showAgents &&
                workflow.agents.map((agent) => (
                  <AppLeaf
                    key={`${workflow.id}:${agent.id}`}
                    app={agent}
                    active={activeAgentSlug === agent.primitiveSlug}
                  />
                ))}
            </SidebarMenuSub>
          </CollapsibleContent>
        )}
      </SidebarMenuSubItem>
    </Collapsible>
  );
}

interface AppLeafProps {
  app: AstroliftAppSummary;
  active: boolean;
}

// Nav primitive → {icon, route prefix}. Agents/workflows/apps have real detail
// routes; function/cronjob/task/bundle have no per-item route yet, so they land
// on the app-detail view (which lists their workload) but keep a distinct icon.
// cronjob = watch (timer), task = checklist, bundle = box (many-in-one).
const PRIMITIVE_NAV: Record<string, { Icon: typeof RocketIcon; prefix: string }> = {
  app: { Icon: RocketIcon, prefix: "/apps" },
  agent: { Icon: BotIcon, prefix: "/agents" },
  workflow: { Icon: WorkflowIcon, prefix: "/workflows" },
  function: { Icon: BoltIcon, prefix: "/apps" },
  cronjob: { Icon: TimerIcon, prefix: "/apps" },
  task: { Icon: ListChecksIcon, prefix: "/apps" },
  bundle: { Icon: BoxIcon, prefix: "/apps" },
};

function AppLeaf({ app, active }: AppLeafProps) {
  const nav = PRIMITIVE_NAV[app.primitiveKind] ?? PRIMITIVE_NAV.app;
  const PrimitiveIcon = nav.Icon;
  return (
    <SidebarMenuSubItem>
      <SidebarMenuSubButton
        asChild
        isActive={active}
        className="!flex !h-auto !min-h-7 !w-full !max-w-none !overflow-visible py-1 !whitespace-normal [&>span:last-child]:!overflow-visible [&>span:last-child]:!text-clip [&>span:last-child]:!whitespace-normal"
      >
        <Link
          href={`${nav.prefix}/${app.primitiveSlug}`}
          aria-current={active ? "page" : undefined}
          className="flex w-full min-w-0 items-start gap-2"
          title={app.name}
        >
          {statusIcon(app.status)}
          <PrimitiveIcon className="text-sidebar-foreground/60 mt-0.5 size-3.5 shrink-0" />
          <span
            className={cn(
              "min-w-0 flex-1 leading-tight break-words whitespace-normal",
              active && "text-sidebar-accent-foreground font-semibold"
            )}
          >
            {app.name}
          </span>
        </Link>
      </SidebarMenuSubButton>
    </SidebarMenuSubItem>
  );
}

interface UnassignedAppsBlockProps {
  label: string;
  apps: AstroliftAppSummary[];
  activeAppSlug: string | null;
}

function UnassignedAppsBlock({ label, apps, activeAppSlug }: UnassignedAppsBlockProps) {
  return (
    <SidebarMenuSubItem>
      <div className="text-2xs text-sidebar-foreground/60 flex items-center gap-2 px-2 py-1 font-medium tracking-wide uppercase">
        <CircleDashedIcon className="size-3" aria-hidden />
        <span className="truncate">{label}</span>
      </div>
      <SidebarMenuSub className="mx-2 px-1.5">
        {apps.map((app) => (
          <AppLeaf key={app.id} app={app} active={activeAppSlug === app.slug} />
        ))}
      </SidebarMenuSub>
    </SidebarMenuSubItem>
  );
}

interface EmptyRowProps {
  href: string;
  label: string;
  cta: string;
}

function EmptyRow({ href, label, cta }: EmptyRowProps) {
  return (
    <SidebarMenuSubItem>
      <SidebarMenuSubButton
        asChild
        className="text-sidebar-foreground/60 hover:text-sidebar-foreground"
      >
        <Link href={href} className="flex flex-col items-start gap-0 py-1">
          <span className="text-2xs tracking-wide uppercase">{label}</span>
          <span className="text-xs">{cta} &rarr;</span>
        </Link>
      </SidebarMenuSubButton>
    </SidebarMenuSubItem>
  );
}
