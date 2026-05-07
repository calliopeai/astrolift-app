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

interface NavItem {
  label: string;
  href: string;
  icon: React.ReactNode;
}

interface NavSection {
  label: string;
  items: NavItem[];
}

const sections: NavSection[] = [
  {
    label: "Platform",
    items: [
      { label: "Overview", href: "/dashboard", icon: <HomeIcon /> },
      { label: "Apps", href: "/apps", icon: <RocketIcon /> },
      { label: "Projects", href: "/projects", icon: <FileBoxIcon /> },
      { label: "Teams", href: "/teams", icon: <UsersIcon /> },
    ],
  },
  {
    label: "Operations",
    items: [
      { label: "Deployments", href: "/deployments", icon: <BoxIcon /> },
      { label: "Workflows", href: "/workflows", icon: <WorkflowIcon /> },
      { label: "Events", href: "/events", icon: <ActivityIcon /> },
      { label: "Audit", href: "/audit", icon: <ScrollTextIcon /> },
    ],
  },
  {
    label: "Infrastructure",
    items: [
      { label: "Clusters", href: "/clusters", icon: <LayersIcon /> },
      { label: "Domains", href: "/domains", icon: <GlobeIcon /> },
      { label: "Providers", href: "/providers", icon: <CloudIcon /> },
      { label: "Webhooks", href: "/webhooks", icon: <WebhookIcon /> },
    ],
  },
  {
    label: "Governance",
    items: [
      { label: "Cost", href: "/cost", icon: <CoinsIcon /> },
      { label: "Quotas", href: "/quotas", icon: <GaugeIcon /> },
      { label: "Members", href: "/members", icon: <ShieldIcon /> },
      { label: "Tokens", href: "/tokens", icon: <KeyIcon /> },
      { label: "Metrics", href: "/metrics", icon: <BarChart3Icon /> },
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

  return (
    <>
      {sections.map((section) => (
        <SidebarGroup key={section.label}>
          <SidebarGroupLabel>{section.label}</SidebarGroupLabel>
          <SidebarMenu>
            {section.items.map((item) => {
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
      ))}
    </>
  );
}
