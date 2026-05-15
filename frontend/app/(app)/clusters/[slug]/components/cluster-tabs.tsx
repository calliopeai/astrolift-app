"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/utils";

type TabKey = "overview" | "status";

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
  const activeKey: TabKey =
    active ?? TABS.find((t) => t.match(pathname, slug))?.key ?? "overview";

  return (
    <nav
      aria-label="Cluster tabs"
      className="border-border -mx-6 flex gap-1 overflow-x-auto border-b px-6"
    >
      {TABS.map((tab) => {
        const isActive = activeKey === tab.key;
        return (
          <Link
            key={tab.key}
            href={tab.href(slug)}
            aria-current={isActive ? "page" : undefined}
            className={cn(
              "relative shrink-0 px-3 py-2.5 text-sm font-medium transition-colors",
              isActive
                ? "text-foreground"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            {tab.label}
            {isActive && (
              <span className="absolute inset-x-1 -bottom-px h-0.5 rounded-full bg-[var(--brand-primary)]" />
            )}
          </Link>
        );
      })}
    </nav>
  );
}
