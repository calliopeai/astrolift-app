"use client";

import {
  ActivityIcon,
  BarChart3Icon,
  BellIcon,
  BoltIcon,
  BoxIcon,
  ClipboardListIcon,
  LayoutDashboardIcon,
  ScrollIcon,
  BookOpenIcon,
  CalendarClockIcon,
  CheckCircle2Icon,
  HammerIcon,
  ChevronRightIcon,
  CreditCardIcon,
  FolderIcon,
  GitBranchIcon,
  GitPullRequestIcon,
  CloudIcon,
  DownloadIcon,
  GlobeIcon,
  KeyRoundIcon,
  LayersIcon,
  LockIcon,
  RocketIcon,
  ScrollTextIcon,
  Settings2Icon,
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
} from "@/components/ui/sidebar";
import { type PermissionCheck, useMyPermissions } from "@/lib/permissions/use-my-permissions";

const COLLAPSED_KEY = "astrolift.nav.collapsed.v1";

interface NavItem {
  label: string;
  href: string;
  icon: React.ReactNode;
  /**
   * Permission(s) required to see this item. Items with no
   * `permission` are visible to every authenticated user (e.g.
   * Settings, which itself routes deeper into per-section guards).
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
   * Coming-soon items: rendered muted with a "Soon" lock affordance
   * and made non-interactive (no <Link>, `aria-disabled`). The route
   * may exist but isn't ready for general navigation. Used for the
   * deferred Run & Observe primitives (Workloads, Workflows).
   */
  disabled?: boolean;
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

interface NavSection {
  label: string;
  /** Flat item list — used when no sub-grouping is needed. */
  items?: NavItem[];
  /** Labeled sub-groups within the section — used for Control where
   * Infrastructure / Org Governance / Org Config are distinct concerns. */
  subGroups?: NavSubGroup[];
}

// ---------------------------------------------------------------------------
// IA restructure (spec 31, step 1) — feature flags
// ---------------------------------------------------------------------------
// The nav is being collapsed from the five BROCS pillars (Build / Run /
// Observe / Control / Secure) down to three groups: a flat Dashboard, a
// "Run & Observe" group, and a "Control" group. This is REVERSIBLE and
// nav-level only — no routes or pages were deleted, and the deferred
// section/item definitions below are kept verbatim.
//
// Everything that is not part of the step-1 surface is gated OFF through
// the single `NAV_FLAGS` map. Each section reads its flag in the
// `sections` array (a falsy flag => the section is omitted from render).
// Re-enabling any deferred pillar is a one-line flip from `false` -> `true`.
//
// Currently live (true): the Run & Observe primitives that ship today
// (Apps, Agents) and the Control group (Infra + Settings + Admin).
// Deferred (false): the BUILD pillar, the OBSERVE pillar, the old flat RUN
// extras (Deployments / Approvals / Skills / Tools / Jobs / Tasks /
// Functions as top-level entries), and SECURE/Zentinelle.
const NAV_FLAGS = {
  build: false, // BUILD pillar: Build / Pipelines / Workflow Definitions
  runExtras: false, // old flat RUN extras now off-nav (see runExtrasSection)
  observe: false, // OBSERVE pillar: ops dashboard dupe + per-primitive signals
  secure: false, // SECURE pillar: Zentinelle GRC handoff
} as const;

// Required-permission annotations mirror the @require_permission
// decorators on the corresponding GraphQL resolvers. When a viewer
// can't read a resource, surfacing the link would just lead to a
// permission-denied empty state, so we hide it entirely.
//
// Note: the tenant hierarchy (Org -> Team -> Project -> App) is
// rendered by `NavTree` above this flat nav. The "Apps" link used to
// live in this Platform section but is reachable via the tree's
// leaves now; "Teams" and "Projects" remain as the flat management
// list pages and have been folded into Settings.

// --- Deferred section definitions (flagged OFF, kept for re-enable) ---
// These are preserved verbatim from the BROCS layout. They are NOT part
// of the step-1 nav; each is included in `sections` only when its
// NAV_FLAGS entry is true.

// BUILD — CI pipelines, image builder, artifact management.
const buildSection: NavSection = {
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
// Tasks / Functions. In the new IA these are no longer top-level nav
// entries (Deployments/Jobs/etc. move under app + primitive surfaces).
const runExtrasSection: NavSection = {
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

// OBSERVE — passive visibility across the signal pyramid. The "Dashboard"
// here is the /ops dupe; the flat Dashboard at the top of the new nav
// supersedes it.
const observeSection: NavSection = {
  label: "Observe",
  items: [
    { label: "Dashboard", href: "/ops", icon: <LayoutDashboardIcon />, permission: "org.read" },
    {
      label: "Deployments",
      href: "/observe/deployments",
      icon: <RocketIcon />,
      permission: "app.read",
    },
    { label: "Agents", href: "/observe/agents", icon: <BoxIcon />, permission: "app.read" },
    {
      label: "Jobs",
      href: "/observe/jobs",
      icon: <CalendarClockIcon />,
      permission: "app.read_logs",
    },
    { label: "Tasks", href: "/observe/tasks", icon: <ClipboardListIcon />, permission: "app.read" },
    {
      label: "Functions",
      href: "/observe/functions",
      icon: <BoltIcon />,
      permission: "app.read",
    },
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
const secureSection: NavSection = {
  label: "Secure",
  items: [{ label: "Zentinelle", href: "/secure/zentinelle", icon: <ShieldCheckIcon /> }],
};

// --- Step-1 nav: three groups ---
//   Dashboard (flat, top)
//   Run & Observe — live primitives (Apps, Agents) + disabled placeholders
//                   (Workloads, Workflows) for primitives not yet shipped.
//   Control — Infra sub-group, Settings (org + RBAC/governance), Admin.
//
// Deferred pillars are spliced in via the NAV_FLAGS guards below: each is a
// one-line `false` -> `true` flip away from rendering again.
const sections: NavSection[] = [
  {
    // Dashboard — flat, no section chrome (empty label => no collapsible
    // wrapper). The single org-wide landing surface.
    label: "",
    items: [
      {
        label: "Dashboard",
        href: "/dashboard",
        icon: <LayoutDashboardIcon />,
        permission: "org.read",
        tourTarget: "dashboard-nav",
      },
    ],
  },
  ...(NAV_FLAGS.build ? [buildSection] : []),
  {
    // Run & Observe — the runtime primitives operators work with daily.
    // Apps + Agents are live; Workloads + Workflows are disabled
    // placeholders (route work pending) rendered with a "Soon" affordance.
    label: "Run & Observe",
    items: [
      { label: "Apps", href: "/apps", icon: <RocketIcon />, permission: "app.read" },
      { label: "Agents", href: "/agents", icon: <BoxIcon />, permission: "app.read" },
      {
        label: "Workloads",
        href: "/workloads",
        icon: <LayersIcon />,
        permission: "app.read",
        disabled: true,
      },
      {
        label: "Workflows",
        href: "/workflows",
        icon: <WorkflowIcon />,
        permission: "app.read",
        disabled: true,
      },
    ],
  },
  ...(NAV_FLAGS.runExtras ? [runExtrasSection] : []),
  ...(NAV_FLAGS.observe ? [observeSection] : []),
  {
    // Control — platform governance. Infra (runtime plane setup) is its own
    // sub-group; Settings owns org config + RBAC/governance (each item
    // keeps its existing destination); Admin links out to the Django admin.
    label: "Control",
    subGroups: [
      {
        label: "Infra",
        items: [
          {
            label: "Clusters",
            href: "/clusters",
            icon: <LayersIcon />,
            permission: { anyOf: ["cluster.register", "provider_plugin.read"] },
            tourTarget: "clusters-nav",
          },
          {
            label: "Domains",
            href: "/domains",
            icon: <GlobeIcon />,
            permission: { anyOf: ["cluster.register", "app.read"] },
          },
          {
            label: "Providers",
            href: "/providers",
            icon: <CloudIcon />,
            permission: "provider_plugin.read",
          },
          {
            label: "Webhooks",
            href: "/webhooks",
            icon: <WebhookIcon />,
            permission: { anyOf: ["webhook.create", "webhook.update"] },
          },
        ],
      },
      {
        // Settings — org settings + RBAC/governance. Destinations are kept
        // as-is from the prior Org Governance / Org Config groups (Members
        // and friends still point at /administration/*, /settings/*, etc.).
        label: "Settings",
        items: [
          {
            label: "Members",
            href: "/administration/members",
            icon: <UsersIcon />,
            permission: "org.manage_members",
          },
          {
            label: "Teams",
            href: "/administration/teams",
            icon: <UsersRoundIcon />,
            permission: "team.read",
          },
          {
            label: "Projects",
            href: "/administration/projects",
            icon: <FolderIcon />,
            permission: "project.read",
          },
          {
            label: "Policies",
            href: "/settings/policies",
            icon: <ShieldIcon />,
            permission: "org.manage_members",
          },
          {
            label: "Permissions",
            href: "/settings/permissions",
            icon: <LockIcon />,
            permission: "org.manage_members",
          },
          {
            label: "Tokens",
            href: "/administration/tokens",
            icon: <KeyRoundIcon />,
            permission: "api_token.create",
          },
          {
            label: "Cost",
            href: "/administration/cost",
            icon: <CreditCardIcon />,
            permission: "billing.read",
          },
          {
            label: "Quotas",
            href: "/administration/quotas",
            icon: <SlidersHorizontalIcon />,
            permission: "org.manage_members",
          },
          {
            label: "Audit",
            href: "/audit",
            icon: <ScrollTextIcon />,
            permission: "audit_log.read",
          },
        ],
      },
      {
        label: "Platform",
        items: [
          {
            // Admin — the Django admin lives outside the Next app and is
            // server-rendered, so it opens via a plain <a> in the same tab.
            //
            // Gate: there is no `is_staff`/`isSuperuser` field on the
            // current viewer surface today — `useMyPermissions` exposes
            // only granted-permission slugs, and the `me` query does not
            // select `isSuperuser` (it exists on the schema's
            // PermissionDiagnosis type, not on Me). We therefore gate on
            // the existing platform-admin proxy `cluster.register` (the
            // same proxy BUILD / Platform Activity used). FOLLOW-UP: add a
            // precise `me.isStaff` (Django is_staff) field and switch this
            // gate to it so non-staff org admins don't see the Django admin.
            label: "Admin",
            href: "/admin",
            icon: <ShieldCheckIcon />,
            permission: "cluster.register",
            external: true,
          },
        ],
      },
    ],
  },
  ...(NAV_FLAGS.secure ? [secureSection] : []),
];

// Control starts collapsed so the daily-driver Run & Observe group sits
// at the top of the viewport without scrolling (#811). Run & Observe stays
// open by default.
const DEFAULT_COLLAPSED: Record<string, boolean> = {
  Control: true,
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
  const { can, loading } = useMyPermissions();

  // Collapsed sections persist in localStorage so refreshes keep
  // the layout the operator chose. Default: every section open.
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

  return (
    // overflow-x-hidden prevents horizontal wobble when sidebar items are
    // wider than the sidebar's collapsed/expanded width during transitions.
    <div className="min-w-0 overflow-x-hidden">
      {sections.map((section) => {
        // Sections use either flat `items` or structured `subGroups`.
        const allItems: NavItem[] = section.subGroups
          ? section.subGroups.flatMap((g) => g.items)
          : (section.items ?? []);

        const visibleItems = allItems.filter(
          (item) =>
            !item.permission ||
            // While permissions are loading, show every item so the
            // sidebar doesn't visibly shrink-and-grow on every refresh.
            // Once the cache is warm, the filter takes effect.
            loading ||
            can(item.permission)
        );
        if (visibleItems.length === 0) return null;

        // Helper to render a list of items as sidebar menu entries.
        const renderItems = (items: NavItem[]) =>
          items
            .filter(
              (item) =>
                !item.permission || loading || can(item.permission)
            )
            .map((item) => {
              // Disabled (coming-soon) items: muted, non-interactive, no
              // navigation. Rendered as a <span> rather than a <Link> so
              // there's no href to follow, and marked `aria-disabled` for
              // assistive tech. A small "Soon" pill stands in for the lock.
              if (item.disabled) {
                return (
                  <SidebarMenuItem key={item.href}>
                    <SidebarMenuButton
                      asChild
                      tooltip={`${item.label} — coming soon`}
                    >
                      <span
                        aria-disabled="true"
                        className="text-muted-foreground/60 pointer-events-none cursor-default"
                      >
                        {item.icon}
                        <span>{item.label}</span>
                        <span className="border-border text-muted-foreground/70 ml-auto rounded-sm border px-1 text-[9px] font-semibold uppercase tracking-wider">
                          Soon
                        </span>
                      </span>
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                );
              }

              const active =
                pathname === item.href ||
                (item.href !== "/" && pathname.startsWith(item.href + "/"));

              // External destinations (e.g. the Django admin) live outside
              // the Next app, so a plain <a> in the same tab is correct —
              // <Link> would attempt to client-route and 404.
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

        // Trust the operator's explicit toggle. Default is open.
        // Unlabeled sections are always visible — no collapsible wrapper.
        if (!section.label) {
          return (
            <SidebarGroup key="__top__">
              <SidebarMenu>{renderItems(section.items ?? [])}</SidebarMenu>
            </SidebarGroup>
          );
        }

        const isOpen = !collapsed[section.label];

        // Content — either flat items or labeled sub-groups.
        const content = section.subGroups ? (
          section.subGroups.map((group) => {
            const groupItems = renderItems(group.items);
            if (!groupItems.length) return null;
            return (
              <div key={group.label}>
                <p className="text-muted-foreground/60 px-2 pb-1 pt-2 text-[10px] font-semibold uppercase tracking-widest">
                  {group.label}
                </p>
                <SidebarMenu>{groupItems}</SidebarMenu>
              </div>
            );
          })
        ) : (
          <SidebarMenu>{renderItems(section.items ?? [])}</SidebarMenu>
        );

        return (
          <Collapsible
            key={section.label}
            open={isOpen}
            onOpenChange={() => toggleSection(section.label)}
            asChild
          >
            <SidebarGroup>
              <SidebarGroupLabel asChild>
                <CollapsibleTrigger className="group/section hover:text-sidebar-foreground flex w-full items-center justify-between">
                  <span className="text-[12px] font-bold uppercase tracking-widest">{section.label}</span>
                  <ChevronRightIcon className="size-3 transition-transform group-data-[state=open]/section:rotate-90" />
                </CollapsibleTrigger>
              </SidebarGroupLabel>
              <CollapsibleContent>{content}</CollapsibleContent>
            </SidebarGroup>
          </Collapsible>
        );
      })}
    </div>
  );
}
