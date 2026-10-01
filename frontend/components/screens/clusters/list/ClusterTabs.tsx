"use client";

import { useTranslations } from "next-intl";
import { usePathname } from "next/navigation";

import { type DetailTab, DetailTabRow } from "@/components/DetailPageTabs";

export type ClusterTabKey = "overview" | "status" | "health" | "activity" | "settings";

interface TabSpec {
  key: ClusterTabKey;
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

/**
 * The cluster's own tabs (spec 44 §5.2: Overview · Status · Health ·
 * Activity · Settings), each its own route. `active` wins; otherwise the
 * pathname picks it.
 */
export function clusterTabs(
  slug: string,
  pathname: string,
  active?: ClusterTabKey,
  label?: (key: ClusterTabKey) => string
): DetailTab[] {
  const activeKey = active ?? TABS.find((t) => t.match(pathname, slug))?.key ?? "overview";
  return TABS.map((tab) => ({
    key: tab.key,
    label: label?.(tab.key) ?? tab.label,
    href: tab.href(slug),
    active: tab.key === activeKey,
  }));
}

interface ClusterTabsProps {
  slug: string;
  active?: ClusterTabKey;
}

/**
 * Link-based tab nav for ``/clusters/[slug]`` (#68), for a page that draws
 * its own header. ClusterHeader carries the same tabs as its one row.
 */
export function ClusterTabs({ slug, active }: ClusterTabsProps) {
  const pathname = usePathname() ?? "";
  const t = useTranslations("clusters.chrome");
  return (
    <div className="-mx-6 min-w-0">
      <DetailTabRow
        ariaLabel={t("tabsAria")}
        tabs={clusterTabs(slug, pathname, active, (key) => t(`tabs.${key}`))}
      />
    </div>
  );
}
