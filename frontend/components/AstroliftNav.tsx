"use client";

import {
  ActivityIcon,
  BarChart3Icon,
  BoxIcon,
  CloudIcon,
  CoinsIcon,
  FileBoxIcon,
  GaugeIcon,
  GlobeIcon,
  HomeIcon,
  KeyIcon,
  LayersIcon,
  RocketIcon,
  ScrollTextIcon,
  Settings2Icon,
  ShieldIcon,
  UsersIcon,
  WebhookIcon,
  WorkflowIcon,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import {
  SidebarGroup,
  SidebarGroupLabel,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar";
import {
  type PermissionCheck,
  useMyPermissions,
} from "@/lib/permissions/use-my-permissions";

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
}

interface NavSection {
  label: string;
  items: NavItem[];
}

// Required-permission annotations mirror the @require_permission
// decorators on the corresponding GraphQL resolvers. When a viewer
// can't read a resource, surfacing the link would just lead to a
// permission-denied empty state, so we hide it entirely.
const sections: NavSection[] = [
  {
    label: "Platform",
    items: [
      { label: "Overview", href: "/dashboard", icon: <HomeIcon /> },
      { label: "Apps", href: "/apps", icon: <RocketIcon />, permission: "app.read" },
      {
        label: "Projects",
        href: "/projects",
        icon: <FileBoxIcon />,
        permission: "project.read",
      },
      { label: "Teams", href: "/teams", icon: <UsersIcon />, permission: "team.read" },
    ],
  },
  {
    label: "Operations",
    items: [
      {
        label: "Deployments",
        href: "/deployments",
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
    ],
  },
  {
    label: "Infrastructure",
    items: [
      {
        label: "Clusters",
        href: "/clusters",
        icon: <LayersIcon />,
        permission: { anyOf: ["cluster.register", "provider_plugin.read"] },
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
    label: "Administration",
    // Platform-admin concerns — RBAC, tokens, cost/quotas. These are
    // the platform's own controls. True GRC (compliance frameworks,
    // attestations, control testing) lives in Zentinelle, which
    // ingests Astrolift's AuditEvent stream via webhook.
    items: [
      {
        label: "Members",
        href: "/members",
        icon: <ShieldIcon />,
        permission: "org.manage_members",
      },
      {
        label: "Tokens",
        href: "/tokens",
        icon: <KeyIcon />,
        permission: { anyOf: ["api_token.create", "api_token.revoke"] },
      },
      {
        label: "Cost",
        href: "/cost",
        icon: <CoinsIcon />,
        permission: "billing.read",
      },
      {
        label: "Quotas",
        href: "/quotas",
        icon: <GaugeIcon />,
        permission: "billing.read",
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
    label: "Account",
    items: [
      { label: "Settings", href: "/settings", icon: <Settings2Icon /> },
    ],
  },
];

export function AstroliftNav() {
  const pathname = usePathname();
  const { can, loading } = useMyPermissions();

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
            can(item.permission),
        );
        if (visibleItems.length === 0) return null;
        return (
          <SidebarGroup key={section.label}>
            <SidebarGroupLabel>{section.label}</SidebarGroupLabel>
            <SidebarMenu>
              {visibleItems.map((item) => {
                const active =
                  pathname === item.href ||
                  (item.href !== "/" && pathname.startsWith(item.href + "/"));
                return (
                  <SidebarMenuItem key={item.href}>
                    <SidebarMenuButton asChild isActive={active} tooltip={item.label}>
                      <Link href={item.href}>
                        {item.icon}
                        <span>{item.label}</span>
                      </Link>
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                );
              })}
            </SidebarMenu>
          </SidebarGroup>
        );
      })}
    </>
  );
}
