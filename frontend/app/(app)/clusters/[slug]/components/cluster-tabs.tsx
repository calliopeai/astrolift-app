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
  const activeKey: TabKey = active ?? TABS.find((t) => t.match(pathname, slug))?.key ?? "overview";

  return (
    // The wrapper handles the right-edge gradient fade — a small,
    // theme-aware "there's more to the right" affordance when the
    // tab strip overflows on narrow screens. The fade sits over the
    // scroll area via pointer-events-none so taps still hit the tab
    // underneath. Snap behavior on the inner nav keeps tab edges
    // aligned to the viewport when the operator swipes.
    <div className="relative -mx-6">
      <nav
        aria-label="Cluster tabs"
        className="border-border flex snap-x snap-mandatory gap-1 overflow-x-auto border-b px-6 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
      >
        {TABS.map((tab) => {
          const isActive = activeKey === tab.key;
          return (
            <Link
              key={tab.key}
              href={tab.href(slug)}
              aria-current={isActive ? "page" : undefined}
              className={cn(
                "relative shrink-0 snap-start px-3 py-2.5 text-sm font-medium transition-colors",
                isActive ? "text-foreground" : "text-muted-foreground hover:text-foreground"
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
      <div
        aria-hidden
        className="from-background pointer-events-none absolute inset-y-0 right-0 w-8 bg-gradient-to-l to-transparent"
      />
    </div>
  );
}
