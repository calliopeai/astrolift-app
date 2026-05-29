"use client";

import {
  ActivityIcon,
  BarChart3Icon,
  BookOpenIcon,
  BoxIcon,
  CalendarClockIcon,
  ChevronRightIcon,
  GitPullRequestIcon,
  CloudIcon,
  DownloadIcon,
  GaugeIcon,
  GlobeIcon,
  HomeIcon,
  LayersIcon,
  RocketIcon,
  ScrollTextIcon,
  Settings2Icon,
  ShieldCheckIcon,
  ShieldIcon,
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

interface NavSection {
  label: string;
  items: NavItem[];
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
    label: "Platform",
    items: [
      { label: "Overview", href: "/dashboard", icon: <HomeIcon /> },
      {
        label: "Deployments",
        href: "/deployments",
        icon: <RocketIcon />,
        permission: "app.read",
      },
    ],
  },
  {
    // Run — active app operations: environments, release workflows,
    // scheduled jobs, and PR preview deployments. Mirrors the "Run"
    // pillar in the Calliope BROC (Build / Run / Observe / Control)
    // positioning: Astrolift is the runtime layer.
    label: "Run",
    items: [
      {
        label: "Environments",
        href: "/environments",
        icon: <CloudIcon />,
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
        label: "Previews",
        href: "/previews",
        icon: <GitPullRequestIcon />,
        permission: "app.read",
      },
    ],
  },
  {
    // Observe — passive visibility: live ops dashboard, event stream,
    // audit trail, and cross-app metrics. Read-only surfaces.
    label: "Observe",
    items: [
      {
        label: "Ops dashboard",
        href: "/ops",
        icon: <GaugeIcon />,
        permission: "org.read",
      },
      {
        label: "Events",
        href: "/events",
        icon: <ActivityIcon />,
        permission: "audit_log.read",
      },
      {
        label: "Audit",
        href: "/audit",
        icon: <ScrollTextIcon />,
        permission: "audit_log.read",
      },
      {
        label: "Metrics",
        href: "/metrics",
        icon: <BarChart3Icon />,
        permission: { anyOf: ["app.read", "app.read_metrics"] },
      },
    ],
  },
  {
    // Control — platform configuration: clusters, DNS, cloud providers,
    // and webhooks. Setup-once surfaces that govern the runtime plane.
    label: "Control",
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
  {
    label: "Manage",
    // Administration (RBAC, tokens, cost/quotas), personal settings,
    // and reference resources grouped under one section. The
    // Administration sub-pages are reached through the horizontal tab
    // bar inside /administration, not the sidebar.
    items: [
      {
        label: "Administration",
        href: "/administration",
        icon: <ShieldIcon />,
        permission: {
          anyOf: [
            "team.read",
            "project.read",
            "org.manage_members",
            "api_token.create",
            "billing.read",
            "app.read",
          ],
        },
      },
      { label: "Settings", href: "/settings", icon: <Settings2Icon /> },
      { label: "Docs", href: "/resources/docs", icon: <BookOpenIcon /> },
      { label: "Downloads", href: "/downloads", icon: <DownloadIcon /> },
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
    <>
      {sections.map((section) => {
        const visibleItems = section.items.filter(
          (item) =>
            !item.permission ||
            // While permissions are loading, show every item so the
            // sidebar doesn't visibly shrink-and-grow on every refresh.
            // Once the cache is warm, the filter takes effect.
            loading ||
            can(item.permission)
        );
        if (visibleItems.length === 0) return null;

        // Trust the operator's explicit toggle. Default is open
        // (every section appears expanded for new operators).
        // The PageHeader breadcrumb still shows where you are if
        // the active item happens to live in a collapsed section.
        const isOpen = !collapsed[section.label];

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
                  <span>{section.label}</span>
                  <ChevronRightIcon className="size-3 transition-transform group-data-[state=open]/section:rotate-90" />
                </CollapsibleTrigger>
              </SidebarGroupLabel>
              <CollapsibleContent>
                <SidebarMenu>
                  {visibleItems.map((item) => {
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
                  })}
                </SidebarMenu>
              </CollapsibleContent>
            </SidebarGroup>
          </Collapsible>
        );
      })}
    </>
  );
}
