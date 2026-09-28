"use client";

import { usePathname } from "next/navigation";

import { DetailTabRow } from "@/components/DetailPageTabs";

type TabKey = "overview" | "status" | "health" | "activity" | "settings";

interface TabSpec {
  key: TabKey;
  label: string;
  href: (slug: string) => string;
  match: (pathname: string, slug: string) => boolean;
}

const TABS: TabSpec[] = [
  {
    key: "overview",
    label: "Overview",
    href: (s) => `/clusters/${s}`,
    match: (p, s) => p === `/clusters/${s}`,
  },
  {
    key: "status",
    label: "Status",
    href: (s) => `/clusters/${s}/status`,
    match: (p, s) => p.startsWith(`/clusters/${s}/status`),
  },
  {
    key: "health",
    label: "Health",
    href: (s) => `/clusters/${s}/health`,
    match: (p, s) => p.startsWith(`/clusters/${s}/health`),
  },
  {
    key: "activity",
    label: "Activity",
    href: (s) => `/clusters/${s}/activity`,
    match: (p, s) => p.startsWith(`/clusters/${s}/activity`),
  },
  {
    key: "settings",
    label: "Settings",
    href: (s) => `/clusters/${s}/settings`,
    match: (p, s) => p.startsWith(`/clusters/${s}/settings`),
  },
];

interface ClusterTabsProps {
  slug: string;
  active?: TabKey;
}

/**
 * Link-based tab nav for ``/clusters/[slug]`` (#68). Mirrors the
 * AppTabs pattern at ``apps/[slug]/components/app-tabs.tsx``. Each
 * tab is a real route; the existing overview detail page keeps its
 * URL and gains a sibling status route under
 * ``/clusters/[slug]/status``.
 */
export function ClusterTabs({ slug, active }: ClusterTabsProps) {
  const pathname = usePathname() ?? "";
  const activeKey: TabKey = active ?? TABS.find((t) => t.match(pathname, slug))?.key ?? "overview";

  return (
    <div className="-mx-6 min-w-0">
      <DetailTabRow
        ariaLabel="Cluster tabs"
        tabs={TABS.map((tab) => ({
          key: tab.key,
          label: tab.label,
          href: tab.href(slug),
          active: tab.key === activeKey,
        }))}
      />
    </div>
  );
}
