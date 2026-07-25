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

import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
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
import { useModules } from "@/graphql/user/user.hooks";
import { LIST_NAV_TREE } from "@/graphql/identity/identity.queries";
import type {
  AstroliftAppStatus,
  AstroliftAppSummary,
  AstroliftNavTree,
  AstroliftNavTreeProjectNode,
  AstroliftNavTreeTeamNode,
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
    return (
      <Loader2Icon
        className="size-3 shrink-0 animate-spin text-warning-fg"
        aria-hidden
      />
    );
  }
  if (status === "failed") {
    return (
      <AlertCircleIcon
        className="size-3 shrink-0 text-danger-fg"
        aria-hidden
      />
    );
  }
  return (
    <span
      aria-hidden
      className={cn(
        "inline-block size-1.5 shrink-0 rounded-full",
        STATUS_DOT_CLASS[status] ?? "bg-slate-400",
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
function nodeKey(kind: "org" | "team" | "project", id: string) {
  return `${kind}:${id}`;
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
  const { data, loading, error } = useQuery<NavTreeResp>(LIST_NAV_TREE, {
    fetchPolicy: "cache-and-network",
  });

  // Active app inferred from the URL. `/apps/<slug>` and any of its
  // children (`/apps/<slug>/workloads/...`) light up the same leaf.
  const activeAppSlug = React.useMemo(() => {
    const match = pathname.match(/^\/apps\/([^/]+)/);
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
    if (!tree || !activeAppSlug) return;
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
          const hit = projectNode.apps.some(
            (a) => a.slug === activeAppSlug,
          );
          if (hit) {
            ensure(nodeKey("team", teamNode.team.id));
            ensure(nodeKey("project", projectNode.project.id));
          }
        }
        if (teamNode.unassignedApps.some((a) => a.slug === activeAppSlug)) {
          ensure(nodeKey("team", teamNode.team.id));
        }
      }
      if (dirty) saveOpenState(next);
      return dirty ? next : prev;
    });
  }, [tree, activeAppSlug]);

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
  if (!modulesLoading && !canView("apps")) return null;

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
        t.projects.reduce((s, p) => s + p.apps.length, 0),
      0,
    ) + tree.unassignedApps.length;

  return (
    <SidebarGroup className="group-data-[collapsible=icon]:hidden">
      <SidebarGroupLabel>Workspace</SidebarGroupLabel>
      <SidebarMenu>
        <Collapsible
          open={orgOpen}
          onOpenChange={() => toggle(orgKey, true)}
          asChild
        >
          <SidebarMenuItem>
            <SidebarMenuButton
              asChild
              tooltip={tree.organization.name}
              className="group/org"
            >
              <div className="flex w-full items-center">
                <Link
                  href="/administration/organization"
                  className="flex flex-1 items-center gap-2 min-w-0"
                  title={tree.organization.name}
                >
                  <Building2Icon className="shrink-0" />
                  <span className="break-words font-medium">
                    {tree.organization.name}
                  </span>
                </Link>
                <CollapsibleTrigger
                  className="ml-auto -mr-1 flex size-5 shrink-0 items-center justify-center rounded-sm hover:bg-sidebar-accent"
                  aria-label={`Toggle ${tree.organization.name}`}
                >
                  <ChevronRightIcon
                    className={cn(
                      "size-3.5 transition-transform",
                      orgOpen && "rotate-90",
                    )}
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
                      open={open}
                      toggle={toggle}
                    />
                  ))
                )}
                {tree.unassignedApps.length > 0 ? (
                  <UnassignedAppsBlock
                    label="Unassigned apps"
                    apps={tree.unassignedApps}
                    activeAppSlug={activeAppSlug}
                  />
                ) : null}
                {totalApps === 0 && tree.teams.length > 0 ? (
                  <EmptyRow
                    href="/apps/new"
                    label="No apps yet"
                    cta="Register first app"
                  />
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
  open: Record<string, boolean>;
  toggle: (key: string, defaultOpen: boolean) => void;
}

function TeamNode({ node, activeAppSlug, open, toggle }: TeamNodeProps) {
  const key = nodeKey("team", node.team.id);
  const isOpen = open[key] ?? true;
  const hasChildren =
    node.projects.length > 0 || node.unassignedApps.length > 0;

  return (
    <Collapsible open={isOpen} onOpenChange={() => toggle(key, true)} asChild>
      <SidebarMenuSubItem>
        <SidebarMenuSubButton
          asChild
          className="group/team !flex !w-full !max-w-none !h-auto !min-h-7 !overflow-visible !whitespace-normal py-1 [&>span:last-child]:!text-clip [&>span:last-child]:!overflow-visible [&>span:last-child]:!whitespace-normal"
        >
          <div className="flex w-full min-w-0 items-start gap-2">
            <UsersIcon className="shrink-0 text-sidebar-foreground/70 mt-0.5" />
            <Link
              href={`/administration/teams?team=${encodeURIComponent(node.team.slug)}`}
              className="flex-1 min-w-0 break-words whitespace-normal text-left leading-tight"
              title={node.team.name}
            >
              {node.team.name}
            </Link>
            <CollapsibleTrigger
              className="-mr-1 translate-x-3.5 flex size-5 shrink-0 items-center justify-center rounded-sm hover:bg-sidebar-accent"
              aria-label={`Toggle ${node.team.name}`}
            >
              <ChevronRightIcon
                className={cn(
                  "size-3 transition-transform",
                  isOpen && "rotate-90",
                )}
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
                    open={open}
                    toggle={toggle}
                  />
                ))}
                {node.unassignedApps.length > 0 ? (
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
          <span aria-hidden className="text-sm leading-none">+</span>
          <span>Add project</span>
        </Link>
      </SidebarMenuSubButton>
    </SidebarMenuSubItem>
  );
}

interface ProjectNodeProps {
  node: AstroliftNavTreeProjectNode;
  activeAppSlug: string | null;
  open: Record<string, boolean>;
  toggle: (key: string, defaultOpen: boolean) => void;
}

function ProjectNode({
  node,
  activeAppSlug,
  open,
  toggle,
}: ProjectNodeProps) {
  const key = nodeKey("project", node.project.id);
  const isOpen = open[key] ?? true;

  return (
    <Collapsible open={isOpen} onOpenChange={() => toggle(key, true)} asChild>
      <SidebarMenuSubItem>
        <SidebarMenuSubButton
          asChild
          className="group/project !flex !w-full !max-w-none !h-auto !min-h-7 !overflow-visible !whitespace-normal py-1 [&>span:last-child]:!text-clip [&>span:last-child]:!overflow-visible [&>span:last-child]:!whitespace-normal"
        >
          <div className="flex w-full min-w-0 items-start gap-2">
            <FileBoxIcon className="shrink-0 text-sidebar-foreground/70 mt-0.5" />
            <Link
              href={`/projects/${encodeURIComponent(node.project.slug)}`}
              className="flex-1 min-w-0 break-words whitespace-normal text-left leading-tight"
              title={node.project.name}
            >
              {node.project.name}
            </Link>
            <CollapsibleTrigger
              className="-mr-1 translate-x-7 flex size-5 shrink-0 items-center justify-center rounded-sm hover:bg-sidebar-accent"
              aria-label={`Toggle ${node.project.name}`}
            >
              <ChevronRightIcon
                className={cn(
                  "size-3 transition-transform",
                  isOpen && "rotate-90",
                )}
              />
            </CollapsibleTrigger>
          </div>
        </SidebarMenuSubButton>
        <CollapsibleContent>
          <SidebarMenuSub className="mx-2 px-1.5">
            {node.apps.length === 0 ? (
              <EmptyRow
                href="/apps/new"
                label="No apps yet"
                cta="Register first app"
              />
            ) : (
              node.apps.map((app) => (
                <AppLeaf
                  key={app.id}
                  app={app}
                  active={activeAppSlug === app.slug}
                />
              ))
            )}
          </SidebarMenuSub>
        </CollapsibleContent>
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
        className="!flex !w-full !max-w-none !h-auto !min-h-7 !overflow-visible !whitespace-normal py-1 [&>span:last-child]:!text-clip [&>span:last-child]:!overflow-visible [&>span:last-child]:!whitespace-normal"
      >
        <Link
          href={`${nav.prefix}/${app.primitiveSlug}`}
          aria-current={active ? "page" : undefined}
          className="flex w-full min-w-0 items-start gap-2"
          title={app.name}
        >
          {statusIcon(app.status)}
          <PrimitiveIcon className="size-3.5 shrink-0 text-sidebar-foreground/60 mt-0.5" />
          <span
            className={cn(
              "flex-1 min-w-0 break-words whitespace-normal leading-tight",
              active && "font-semibold text-sidebar-accent-foreground",
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

function UnassignedAppsBlock({
  label,
  apps,
  activeAppSlug,
}: UnassignedAppsBlockProps) {
  return (
    <SidebarMenuSubItem>
      <div className="flex items-center gap-2 px-2 py-1 text-2xs font-medium text-sidebar-foreground/60 uppercase tracking-wide">
        <CircleDashedIcon className="size-3" aria-hidden />
        <span className="truncate">{label}</span>
      </div>
      <SidebarMenuSub className="mx-2 px-1.5">
        {apps.map((app) => (
          <AppLeaf
            key={app.id}
            app={app}
            active={activeAppSlug === app.slug}
          />
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
          <span className="text-2xs uppercase tracking-wide">{label}</span>
          <span className="text-xs">{cta} &rarr;</span>
        </Link>
      </SidebarMenuSubButton>
    </SidebarMenuSubItem>
  );
}
