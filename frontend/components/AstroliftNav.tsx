"use client";

import {
  ActivityIcon,
  BarChart3Icon,
  BellIcon,
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
  FingerprintIcon,
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
  ServerIcon,
  Settings2Icon,
  ShieldCheckIcon,
  ShieldIcon,
  SlidersHorizontalIcon,
  UsersIcon,
  UsersRoundIcon,
  WebhookIcon,
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

// Required-permission annotations mirror the @require_permission
// decorators on the corresponding GraphQL resolvers. When a viewer
// can't read a resource, surfacing the link would just lead to a
// permission-denied empty state, so we hide it entirely.
//
// Note: the tenant hierarchy (Org -> Team -> Project -> App) is
// rendered by `NavTree` above this flat nav. The "Apps" link used to
// live in this Platform section but is reachable via the tree's
// leaves now; "Teams" and "Projects" remain as the flat management
// list pages and have been folded into Administration.
const sections: NavSection[] = [
  {
    // Build — CI pipelines, image builder, artifact management.
    // This pillar is on the product roadmap; the page is an enable/
    // onboarding gateway following the same pattern as Zentinelle.
    // Gated on cluster.register so only platform admins see it.
    label: "Build",
    items: [
      {
        label: "Build",
        href: "/build",
        icon: <HammerIcon />,
        permission: "cluster.register",
      },
      {
        label: "Workflow Definitions",
        href: "/workflows",
        icon: <WorkflowIcon />,
        permission: "app.read",
      },
    ],
  },
  {
    // Run — the three runtime primitives Astrolift manages:
    //   Apps (Deployments + gate management)
    //   Agents (agent dispatch and scheduling)
    //   Workflows (automation workflow instances)
    // Environments and Previews are app-scoped → app tab bar.
    // Workflow definitions (authoring) live in BUILD.
    label: "Run",
    items: [
      {
        label: "Deployments",
        href: "/deployments",
        icon: <RocketIcon />,
        permission: "app.read",
      },
      {
        label: "Approvals",
        href: "/approvals",
        icon: <CheckCircle2Icon />,
        permission: "app.approve_deploy",
      },
      {
        label: "Agents",
        href: "/agents",
        icon: <BoxIcon />,
        permission: "app.read",
      },
      {
        label: "Workflows",
        href: "/workflows",
        icon: <WorkflowIcon />,
        permission: "app.read",
      },
      {
        label: "Jobs",
        href: "/jobs",
        icon: <CalendarClockIcon />,
        permission: "app.read_logs",
      },
      {
        label: "Tasks",
        href: "/tasks",
        icon: <ClipboardListIcon />,
        permission: "app.read",
      },
    ],
  },
  {
    // Observe — passive visibility across the signal pyramid:
    // SLO dashboard → Metrics → Logs → Traces → Events → Alerts.
    // Audit (governance evidence) lives in Control / Org Governance.
    label: "Observe",
    items: [
      {
        label: "Dashboard",
        href: "/ops",
        icon: <LayoutDashboardIcon />,
        permission: "org.read",
      },
      {
        label: "Metrics",
        href: "/metrics",
        icon: <BarChart3Icon />,
        permission: { anyOf: ["app.read", "app.read_metrics"] },
      },
      {
        label: "Logs",
        href: "/logs",
        icon: <ScrollIcon />,
        permission: "app.read_logs",
      },
      {
        label: "Traces",
        href: "/traces",
        icon: <GitBranchIcon />,
        permission: "app.read",
      },
      {
        label: "Events",
        href: "/events",
        icon: <ActivityIcon />,
        permission: "audit_log.read",
      },
      {
        label: "Alerts",
        href: "/alerts",
        icon: <BellIcon />,
        permission: "org.read",
      },
      {
        label: "Platform Activity",
        href: "/platform-activity",
        icon: <ActivityIcon />,
        permission: "cluster.register",
      },
    ],
  },
  {
    // Control — platform governance split into three concerns:
    // Infrastructure (runtime plane setup), Org Governance (identity,
    // access, compliance), and Org Config (integrations + domain config).
    label: "Control",
    subGroups: [
      {
        label: "Infrastructure",
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
        label: "Org Governance",
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
        label: "Org Config",
        items: [
          {
            label: "Identity Provider",
            href: "/settings/identity-provider",
            icon: <FingerprintIcon />,
            permission: "org.manage_members",
          },
          {
            label: "Source Providers",
            href: "/settings/source-providers",
            icon: <GitBranchIcon />,
            permission: "org.manage_members",
          },
          {
            label: "Managed Domains",
            href: "/settings/managed-domains",
            icon: <ServerIcon />,
            permission: "provider_plugin.configure",
          },
        ],
      },
    ],
  },
  {
    // Secure — the Zentinelle integration gateway. Zentinelle is the
    // Control + Observe + Secure pillar in the BROCS stack. This entry
    // is the handoff from Astrolift (Run) into the GRC/security plane.
    label: "Secure",
    items: [
      {
        label: "Zentinelle",
        href: "/secure/zentinelle",
        icon: <ShieldCheckIcon />,
      },
    ],
  },
];

function loadCollapsedState(): Record<string, boolean> {
  if (typeof window === "undefined") return {};
  try {
    const raw = window.localStorage.getItem(COLLAPSED_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    return typeof parsed === "object" && parsed !== null ? parsed : {};
  } catch {
    return {};
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
  const [collapsed, setCollapsed] = React.useState<Record<string, boolean>>({});
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
              const active =
                pathname === item.href ||
                (item.href !== "/" && pathname.startsWith(item.href + "/"));
              return (
                <SidebarMenuItem key={item.href}>
                  <SidebarMenuButton asChild isActive={active} tooltip={item.label}>
                    <Link href={item.href} data-onboarding-tour={item.tourTarget}>
                      {item.icon}
                      <span>{item.label}</span>
                    </Link>
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
