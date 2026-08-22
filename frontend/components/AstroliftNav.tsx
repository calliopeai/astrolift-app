"use client";

import {
  ActivityIcon,
  BellIcon,
  BoltIcon,
  BotIcon,
  Building2Icon,
  ClipboardListIcon,
  LayoutDashboardIcon,
  BookOpenIcon,
  CalendarClockIcon,
  CheckCircle2Icon,
  HammerIcon,
  ChevronRightIcon,
  CreditCardIcon,
  FolderIcon,
  GaugeIcon,
  GitBranchIcon,
  CloudIcon,
  GlobeIcon,
  KeyRoundIcon,
  LayersIcon,
  LockIcon,
  NetworkIcon,
  RocketIcon,
  ScrollTextIcon,
  ShieldCheckIcon,
  ShieldIcon,
  SlidersHorizontalIcon,
  UsersIcon,
  UsersRoundIcon,
  WebhookIcon,
  WorkflowIcon,
  WrenchIcon,
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
import { type ModuleKey, useModules } from "@/graphql/user/user.hooks";
import type { PermissionCheck } from "@/lib/permissions/use-my-permissions";

const COLLAPSED_KEY = "astrolift.nav.collapsed.v1";

interface NavItem {
  label: string;
  href: string;
  icon: React.ReactNode;
  /**
   * Permission(s) required to see this item. Items with no
   * `permission` are visible to every authenticated user (e.g.
   * Settings, which itself routes deeper into per-section guards).
   *
   * NOTE (spec 36 §1.3): the rendered nav no longer uses this field —
   * modules are gated by the server-authoritative `me.modules.canView`
   * (see `moduleKey` below) and finer gating lives in-page (`<Can>`).
   * The field survives only on the deferred (flag-off) section
   * definitions kept on disk below. The frontend carries no permission
   * *logic* for the switcher — it renders the server's answer.
   */
  permission?: PermissionCheck;
  /**
   * Opt-in marker the first-run spotlight tour (#452) reads via
   * ``document.querySelector`` to anchor a popover. The string is
   * applied verbatim as a ``data-onboarding-tour`` attribute on the
   * rendered <Link>. Items without a marker get no attribute and
   * are invisible to the tour.
   */
  tourTarget?: string;
  /**
   * External destination rendered with a plain <a> in the same tab
   * (e.g. the Django admin at /admin, which is server-rendered outside
   * the Next app). Next's <Link> would try to client-route and 404.
   */
  external?: boolean;
}

interface NavSubGroup {
  /** Short muted label rendered above the group's items (e.g. "Infrastructure"). */
  label: string;
  items: NavItem[];
}

interface ModuleEntry {
  /** Top-level label rendered in the switcher (e.g. "Apps", "Admin"). */
  label: string;
  /**
   * Server `me.modules` key gating this module's top-level visibility
   * (spec 36 §1.2). `undefined` => always rendered (Dashboard only —
   * it has no `me.modules` entry; `org.read` is implicit).
   */
  moduleKey?: ModuleKey;
  icon: React.ReactNode;
  /** Landing route for a flat module entry (Dashboard, Apps, Agents, Workflows). */
  href?: string;
  tourTarget?: string;
  /**
   * Flat child links rendered as an indented sub-nav directly beneath a
   * landing-link module (Apps → Deployments/Approvals, Workflows →
   * Jobs/Tasks/Functions). Only meaningful alongside `href`.
   */
  items?: NavItem[];
  /**
   * Labeled sub-groups for a module rendered as a collapsible section
   * (Admin). Mutually exclusive with `href` — a module is either a flat
   * landing link or a collapsible container of sub-nav.
   */
  subGroups?: NavSubGroup[];
}

// ---------------------------------------------------------------------------
// Entity-module switcher (spec 34 re-shell, spec 36 Phase 1)
// ---------------------------------------------------------------------------
// The top-level nav is the FIVE entity modules, gated by the
// server-authoritative `me.modules.canView` (NOT client-side permission
// strings). This replaces the prior BROCS/`NAV_FLAGS` three-group layout:
//
//   Dashboard  — always rendered (no `me.modules` key).
//   Apps       — iff `modules.apps.canView`.
//   Agents     — iff `modules.agents.canView`.
//   Workflows  — iff `modules.workflows.canView`.
//   Admin      — iff `modules.admin.canView`. The former `Control` group,
//                restructured into four sub-groups: Organization /
//                Infrastructure / Usage & Governance / Platform Signals.
//
// The former `NAV_FLAGS`-gated Build / Observe / Secure / runExtras sections
// and the Run & Observe "Workloads" placeholder are dropped FROM THE SWITCHER
// (their routes are not deleted — cuts are Phase 5). The deferred section
// definitions below are preserved verbatim for re-enable / route reference.

// ---------------------------------------------------------------------------
// Deferred section definitions (NOT in the switcher — kept for reference /
// re-enable; their routes remain reachable, deletions are Phase 5).
// ---------------------------------------------------------------------------
// These BROCS pillars are off-nav under the module switcher but their flags
// and definitions are preserved verbatim (spec 36 §1.2 — "they stay flag-off;
// their routes remain reachable, not deleted"). Re-enabling any of them is a
// later-phase decision, not a one-line flip into this switcher.
const NAV_FLAGS = {
  build: false, // BUILD pillar: Build / Pipelines / Workflow Definitions
  runExtras: false, // old flat RUN extras now off-nav (see runExtrasSection)
  observe: false, // OBSERVE pillar: ops dashboard dupe + per-primitive signals
  secure: false, // SECURE pillar: Zentinelle GRC handoff
  platform: false, // former Admin > Platform group (Django admin escape hatch)
} as const;

// BUILD — CI pipelines, image builder, artifact management.
const buildSection: { label: string; items: NavItem[] } = {
  label: "Build",
  items: [
    { label: "Build", href: "/build", icon: <HammerIcon />, permission: "cluster.register" },
    { label: "Pipelines", href: "/pipelines", icon: <GitBranchIcon />, permission: "pipeline.read" },
    {
      label: "Workflow Definitions",
      href: "/workflows",
      icon: <WorkflowIcon />,
      permission: "app.read",
    },
  ],
};

// Old flat RUN extras — Deployments / Approvals / Skills / Tools / Jobs /
// Tasks / Functions. In the new IA these are no longer top-level nav entries.
const runExtrasSection: { label: string; items: NavItem[] } = {
  label: "Run",
  items: [
    { label: "Deployments", href: "/deployments", icon: <RocketIcon />, permission: "app.read" },
    {
      label: "Approvals",
      href: "/approvals",
      icon: <CheckCircle2Icon />,
      permission: "app.approve_deploy",
    },
    { label: "Skills", href: "/agents/skills", icon: <BookOpenIcon />, permission: "app.read" },
    { label: "Tools", href: "/agents/tools", icon: <WrenchIcon />, permission: "app.read" },
    { label: "Jobs", href: "/jobs", icon: <CalendarClockIcon />, permission: "app.read_logs" },
    { label: "Tasks", href: "/tasks", icon: <ClipboardListIcon />, permission: "app.read" },
    { label: "Functions", href: "/functions", icon: <BoltIcon />, permission: "app.read" },
  ],
};

// OBSERVE — passive visibility across the signal pyramid.
const observeSection: { label: string; items: NavItem[] } = {
  label: "Observe",
  items: [
    { label: "Dashboard", href: "/ops", icon: <LayoutDashboardIcon />, permission: "org.read" },
    { label: "Events", href: "/events", icon: <ActivityIcon />, permission: "audit_log.read" },
    { label: "Alerts", href: "/alerts", icon: <BellIcon />, permission: "org.read" },
    {
      label: "Platform Activity",
      href: "/platform-activity",
      icon: <ActivityIcon />,
      permission: "cluster.register",
    },
  ],
};

// SECURE — the Zentinelle integration gateway (GRC/security plane handoff).
const secureSection: { label: string; items: NavItem[] } = {
  label: "Secure",
  items: [{ label: "Zentinelle", href: "/secure/zentinelle", icon: <ShieldCheckIcon /> }],
};

// Former Admin > Platform group. The Django admin lives outside the Next
// app and is server-rendered, so it opens via a plain <a> in the same tab.
// Not in the four restructured Admin sub-groups (spec 36 §1.2) — kept here
// flag-off; the /app/admin route itself remains reachable directly. Gate
// kept as the existing platform-admin proxy `cluster.register` (there is
// no `me.isStaff` field yet).
const platformSection: { label: string; items: NavItem[] } = {
  label: "Platform",
  items: [
    {
      label: "Admin",
      href: "/app/admin",
      icon: <ShieldCheckIcon />,
      permission: "cluster.register",
      external: true,
    },
  ],
};

// Retention anchor: keeps the flags + deferred definitions live for
// re-enable / Phase-5 cut planning without tripping no-unused-vars. The
// flags are all `false`, so this resolves to an empty list — the deferred
// sections are intentionally NOT rendered by the module switcher.
const DEFERRED_SECTIONS = [
  ...(NAV_FLAGS.build ? [buildSection] : []),
  ...(NAV_FLAGS.runExtras ? [runExtrasSection] : []),
  ...(NAV_FLAGS.observe ? [observeSection] : []),
  ...(NAV_FLAGS.secure ? [secureSection] : []),
  ...(NAV_FLAGS.platform ? [platformSection] : []),
];
void DEFERRED_SECTIONS;

// ---------------------------------------------------------------------------
// The five modules.
// ---------------------------------------------------------------------------
// Dashboard / Agents are flat landing links; Apps and Workflows are landing
// links with an indented child sub-nav. Admin is a collapsible container of
// four labeled sub-groups (§1.2).
const modules: ModuleEntry[] = [
  {
    // Dashboard — always rendered (no `me.modules` key; `org.read` implicit).
    label: "Dashboard",
    icon: <LayoutDashboardIcon />,
    href: "/dashboard",
    tourTarget: "dashboard-nav",
  },
  {
    label: "Apps",
    moduleKey: "apps",
    icon: <RocketIcon />,
    href: "/apps",
    items: [
      { label: "Deployments", href: "/deployments", icon: <RocketIcon /> },
      { label: "Approvals", href: "/approvals", icon: <CheckCircle2Icon /> },
    ],
  },
  {
    label: "Agents",
    moduleKey: "agents",
    icon: <BotIcon />,
    href: "/agents",
    items: [{ label: "Fleet Map", href: "/fleet/map", icon: <NetworkIcon /> }],
  },
  {
    label: "Workflows",
    moduleKey: "workflows",
    icon: <WorkflowIcon />,
    href: "/workflows",
    items: [
      { label: "Jobs", href: "/jobs", icon: <CalendarClockIcon /> },
      { label: "Tasks", href: "/tasks", icon: <ClipboardListIcon /> },
      { label: "Functions", href: "/functions", icon: <BoltIcon /> },
    ],
  },
  {
    // Admin — the former `Control` group restructured into four labeled
    // sub-groups (spec 36 §1.2 Phase 2). Top-level visibility is
    // `modules.admin.canView`; finer gating is in-page (`<Can>`), not
    // per-item nav permission strings. Moved /administration/* routes
    // are created by the B2 route pass — hrefs point at the targets.
    label: "Admin",
    moduleKey: "admin",
    icon: <ShieldCheckIcon />,
    subGroups: [
      {
        label: "Organization",
        items: [
          {
            label: "Organization",
            href: "/administration/organization",
            icon: <Building2Icon />,
          },
          { label: "Members", href: "/administration/members", icon: <UsersIcon /> },
          { label: "Teams", href: "/administration/teams", icon: <UsersRoundIcon /> },
          { label: "Projects", href: "/administration/projects", icon: <FolderIcon /> },
          { label: "Policies", href: "/administration/policies", icon: <ShieldIcon /> },
          { label: "Permissions", href: "/administration/permissions", icon: <LockIcon /> },
        ],
      },
      {
        label: "Infrastructure",
        items: [
          {
            label: "Clusters",
            href: "/clusters",
            icon: <LayersIcon />,
            tourTarget: "clusters-nav",
          },
          { label: "Domains", href: "/domains", icon: <GlobeIcon /> },
          { label: "Providers", href: "/providers", icon: <CloudIcon /> },
          { label: "Webhooks", href: "/webhooks", icon: <WebhookIcon /> },
        ],
      },
      {
        label: "Usage & Governance",
        items: [
          { label: "Metrics", href: "/administration/metrics", icon: <GaugeIcon /> },
          // /tokens stays top-level canonical (#893) — this entry points AT it.
          { label: "API Keys", href: "/tokens", icon: <KeyRoundIcon /> },
          { label: "Audit", href: "/administration/audit", icon: <ScrollTextIcon /> },
        ],
      },
      {
        label: "Platform Signals",
        items: [
          {
            label: "Platform Activity",
            href: "/platform-activity",
            icon: <ActivityIcon />,
          },
          { label: "Alerts", href: "/alerts", icon: <BellIcon /> },
          { label: "Events", href: "/events", icon: <ActivityIcon /> },
        ],
      },
    ],
  },
];

// Admin starts collapsed so the daily-driver modules sit at the top of the
// viewport without scrolling (#811).
const DEFAULT_COLLAPSED: Record<string, boolean> = {
  Admin: true,
};

function loadCollapsedState(): Record<string, boolean> {
  if (typeof window === "undefined") return DEFAULT_COLLAPSED;
  try {
    const raw = window.localStorage.getItem(COLLAPSED_KEY);
    // If the user has never saved a preference, use the defaults.
    if (!raw) return DEFAULT_COLLAPSED;
    const parsed = JSON.parse(raw);
    return typeof parsed === "object" && parsed !== null ? parsed : DEFAULT_COLLAPSED;
  } catch {
    return DEFAULT_COLLAPSED;
  }
}

function saveCollapsedState(state: Record<string, boolean>) {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(COLLAPSED_KEY, JSON.stringify(state));
  } catch {
    // localStorage might be disabled — degrade silently.
  }
}

export function AstroliftNav() {
  const pathname = usePathname();
  // All nav visibility comes from the server-authoritative `me.modules`
  // (spec 36 §1.3) — module gating only, no per-item permission strings.
  // Finer gating lives in-page (`<Can>`).
  const { canView, loading: modulesLoading } = useModules();

  // Collapsed sections persist in localStorage so refreshes keep the layout
  // the operator chose.
  const [collapsed, setCollapsed] = React.useState<Record<string, boolean>>(DEFAULT_COLLAPSED);
  React.useEffect(() => {
    setCollapsed(loadCollapsedState());
  }, []);

  function toggleSection(label: string) {
    setCollapsed((prev) => {
      const next = { ...prev, [label]: !prev[label] };
      saveCollapsedState(next);
      return next;
    });
  }

  // Render a list of sub-nav items as sidebar menu entries. Items are not
  // permission-filtered — visibility is the containing module's `canView`
  // (spec 36 §1.3); in-page guards handle the rest.
  const renderItems = (items: NavItem[]) =>
    items
      .map((item) => {
        const active =
          pathname === item.href ||
          (item.href !== "/" && pathname.startsWith(item.href + "/"));

        // External destinations (e.g. the Django admin) live outside the
        // Next app, so a plain <a> in the same tab is correct — <Link> would
        // attempt to client-route and 404.
        const linkEl = item.external ? (
          <a href={item.href} data-onboarding-tour={item.tourTarget}>
            {item.icon}
            <span>{item.label}</span>
          </a>
        ) : (
          <Link href={item.href} data-onboarding-tour={item.tourTarget}>
            {item.icon}
            <span>{item.label}</span>
          </Link>
        );

        return (
          <SidebarMenuItem key={item.href}>
            <SidebarMenuButton asChild isActive={active} tooltip={item.label}>
              {linkEl}
            </SidebarMenuButton>
          </SidebarMenuItem>
        );
      });

  return (
    // overflow-x-hidden prevents horizontal wobble when sidebar items are
    // wider than the sidebar's collapsed/expanded width during transitions.
    <div className="min-w-0 overflow-x-hidden">
      {modules.map((mod) => {
        // Top-level gate: Dashboard (no key) always shows; every other module
        // shows iff the server says `canView`. While `me.modules` is loading
        // we render so the sidebar doesn't shrink-and-grow on refresh.
        const visible =
          mod.moduleKey === undefined || modulesLoading || canView(mod.moduleKey);
        if (!visible) return null;

        // Flat module (Dashboard / Apps / Agents / Workflows) — a landing
        // link, optionally with an indented child sub-nav (Apps →
        // Deployments/Approvals, Workflows → Jobs/Tasks/Functions).
        if (mod.href) {
          const active =
            pathname === mod.href || pathname.startsWith(mod.href + "/");
          return (
            <SidebarGroup key={mod.label}>
              <SidebarMenu>
                <SidebarMenuItem>
                  <SidebarMenuButton asChild isActive={active} tooltip={mod.label}>
                    <Link href={mod.href} data-onboarding-tour={mod.tourTarget}>
                      {mod.icon}
                      <span>{mod.label}</span>
                    </Link>
                  </SidebarMenuButton>
                  {mod.items && mod.items.length > 0 && (
                    <SidebarMenuSub>
                      {mod.items.map((item) => {
                        const subActive =
                          pathname === item.href || pathname.startsWith(item.href + "/");
                        return (
                          <SidebarMenuSubItem key={item.href}>
                            <SidebarMenuSubButton asChild isActive={subActive}>
                              <Link href={item.href}>
                                {item.icon}
                                <span>{item.label}</span>
                              </Link>
                            </SidebarMenuSubButton>
                          </SidebarMenuSubItem>
                        );
                      })}
                    </SidebarMenuSub>
                  )}
                </SidebarMenuItem>
              </SidebarMenu>
            </SidebarGroup>
          );
        }

        // Collapsible module (Admin) — labeled sub-groups of internal nav.
        const subGroups = mod.subGroups ?? [];
        const renderedGroups = subGroups
          .map((group) => ({ group, items: renderItems(group.items) }))
          .filter(({ items }) => items.length > 0);
        // Guard against an empty shell (a module defined with no sub-items).
        if (renderedGroups.length === 0) return null;

        const isOpen = !collapsed[mod.label];

        return (
          <Collapsible
            key={mod.label}
            open={isOpen}
            onOpenChange={() => toggleSection(mod.label)}
            asChild
          >
            <SidebarGroup>
              <SidebarGroupLabel asChild>
                <CollapsibleTrigger className="group/section hover:text-sidebar-foreground flex w-full items-center justify-between">
                  <span className="text-xs font-bold uppercase tracking-widest">{mod.label}</span>
                  <ChevronRightIcon className="size-3 transition-transform group-data-[state=open]/section:rotate-90" />
                </CollapsibleTrigger>
              </SidebarGroupLabel>
              <CollapsibleContent>
                {renderedGroups.map(({ group, items }) => (
                  <div key={group.label}>
                    <p className="text-muted-foreground/60 px-2 pb-1 pt-2 text-2xs font-semibold uppercase tracking-widest">
                      {group.label}
                    </p>
                    <SidebarMenu>{items}</SidebarMenu>
                  </div>
                ))}
              </CollapsibleContent>
            </SidebarGroup>
          </Collapsible>
        );
      })}
    </div>
  );
}
