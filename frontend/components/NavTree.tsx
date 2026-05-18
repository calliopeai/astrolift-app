"use client";

import { useQuery } from "@apollo/client/react";
import {
  AlertCircleIcon,
  Building2Icon,
  ChevronRightIcon,
  CircleDashedIcon,
  FileBoxIcon,
  Loader2Icon,
  RocketIcon,
  UsersIcon,
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
  ready: "bg-emerald-500",
  provisioning: "bg-amber-500",
  pending: "bg-slate-400",
  failed: "bg-rose-500",
};

function statusIcon(status: AstroliftAppStatus) {
  if (status === "provisioning") {
    return (
      <Loader2Icon
        className="size-3 shrink-0 animate-spin text-amber-500"
        aria-hidden
      />
    );
  }
  if (status === "failed") {
    return (
      <AlertCircleIcon
        className="size-3 shrink-0 text-rose-500"
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
                  href="/settings/organization"
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
                  <ChevronRightIcon className="size-3.5 transition-transform group-data-[state=open]/org:rotate-90" />
                </CollapsibleTrigger>
              </div>
            </SidebarMenuButton>
            <CollapsibleContent>
              <SidebarMenuSub>
                {tree.teams.length === 0 ? (
                  <EmptyRow
                    href="/teams"
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
          className="group/team !w-full !h-auto !min-h-7 !overflow-visible py-1 [&_span]:!whitespace-normal [&_span]:!overflow-visible"
        >
          <div className="flex w-full items-start">
            <Link
              href={`/teams?team=${encodeURIComponent(node.team.slug)}`}
              className="flex flex-1 items-start gap-2 min-w-0"
              title={node.team.name}
            >
              <UsersIcon className="shrink-0 text-sidebar-foreground/70 mt-0.5" />
              <span className="break-words whitespace-normal flex-1 min-w-0">
                {node.team.name}
              </span>
            </Link>
            <CollapsibleTrigger
              className="ml-auto -mr-1 flex size-5 shrink-0 items-center justify-center rounded-sm hover:bg-sidebar-accent"
              aria-label={`Toggle ${node.team.name}`}
            >
              <ChevronRightIcon className="size-3 transition-transform group-data-[state=open]/team:rotate-90" />
            </CollapsibleTrigger>
          </div>
        </SidebarMenuSubButton>
        <CollapsibleContent>
          <SidebarMenuSub>
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
          className="group/project !w-full !h-auto !min-h-7 !overflow-visible py-1 [&_span]:!whitespace-normal [&_span]:!overflow-visible"
        >
          <div className="flex w-full items-start">
            <Link
              href={`/projects/${encodeURIComponent(node.project.slug)}`}
              className="flex flex-1 items-start gap-2 min-w-0"
              title={node.project.name}
            >
              <FileBoxIcon className="shrink-0 text-sidebar-foreground/70 mt-0.5" />
              <span className="break-words whitespace-normal flex-1 min-w-0">
                {node.project.name}
              </span>
            </Link>
            <CollapsibleTrigger
              className="ml-auto -mr-1 flex size-5 shrink-0 items-center justify-center rounded-sm hover:bg-sidebar-accent"
              aria-label={`Toggle ${node.project.name}`}
            >
              <ChevronRightIcon className="size-3 transition-transform group-data-[state=open]/project:rotate-90" />
            </CollapsibleTrigger>
          </div>
        </SidebarMenuSubButton>
        <CollapsibleContent>
          <SidebarMenuSub>
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

function AppLeaf({ app, active }: AppLeafProps) {
  return (
    <SidebarMenuSubItem>
      <SidebarMenuSubButton
        asChild
        isActive={active}
        className="!w-full !h-auto !min-h-7 !overflow-visible py-1 [&>span:last-child]:!whitespace-normal [&>span:last-child]:!overflow-visible [&>span:last-child]:!text-clip"
      >
        <Link
          href={`/apps/${app.slug}`}
          aria-current={active ? "page" : undefined}
          className="flex items-start gap-2 min-w-0 w-full"
          title={app.name}
        >
          {statusIcon(app.status)}
          <RocketIcon className="size-3.5 shrink-0 text-sidebar-foreground/60 mt-0.5" />
          <span
            className={cn(
              "!whitespace-normal !overflow-visible !text-clip break-words flex-1 min-w-0",
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
      <div className="flex items-center gap-2 px-2 py-1 text-[10px] font-medium text-sidebar-foreground/60 uppercase tracking-wide">
        <CircleDashedIcon className="size-3" aria-hidden />
        <span className="truncate">{label}</span>
      </div>
      <SidebarMenuSub>
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
          <span className="text-[11px] uppercase tracking-wide">{label}</span>
          <span className="text-xs">{cta} &rarr;</span>
        </Link>
      </SidebarMenuSubButton>
    </SidebarMenuSubItem>
  );
}
