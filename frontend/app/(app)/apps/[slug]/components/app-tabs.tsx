"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/utils";

type TabKey = "overview" | "deployments" | "domains" | "secrets" | "members" | "settings";

interface TabSpec {
  key: TabKey;
  label: string;
  href: (slug: string) => string;
  /** Sub-paths that should still highlight this tab when matched. */
  match: (pathname: string, slug: string) => boolean;
}

const TABS: TabSpec[] = [
  {
    key: "overview",
    label: "Overview",
    href: (s) => `/apps/${s}`,
    match: (p, s) => p === `/apps/${s}`,
  },
  {
    key: "deployments",
    label: "Deployments",
    href: (s) => `/apps/${s}/environments`,
    match: (p, s) =>
      p.startsWith(`/apps/${s}/environments`) ||
      p.startsWith(`/apps/${s}/workloads`) ||
      p.startsWith(`/apps/${s}/jobs`) ||
      p.startsWith(`/apps/${s}/commands`),
  },
  {
    key: "domains",
    label: "Domains",
    href: (s) => `/apps/${s}/domains`,
    match: (p, s) => p.startsWith(`/apps/${s}/domains`),
  },
  {
    key: "secrets",
    label: "Secrets",
    href: (s) => `/apps/${s}/secrets`,
    match: (p, s) => p.startsWith(`/apps/${s}/secrets`) || p.startsWith(`/apps/${s}/tokens`),
  },
  {
    key: "members",
    label: "Members",
    href: (s) => `/apps/${s}/members`,
    match: (p, s) => p.startsWith(`/apps/${s}/members`),
  },
  {
    key: "settings",
    label: "Settings",
    href: (s) => `/apps/${s}/config`,
    match: (p, s) =>
      p.startsWith(`/apps/${s}/config`) ||
      p.startsWith(`/apps/${s}/manifest`) ||
      p.startsWith(`/apps/${s}/webhooks`) ||
      p.startsWith(`/apps/${s}/managed-services`),
  },
];

interface AppTabsProps {
  slug: string;
  /** Explicit override for the active tab; otherwise inferred from pathname. */
  active?: TabKey;
}

/**
 * Link-based tab nav for `/apps/[slug]`. Each tab is a real route — the
 * existing sibling subroutes (`environments/`, `domains/`, etc.) still own
 * their pages; this just gives the page a tabbed top-level identity.
 */
export function AppTabs({ slug, active }: AppTabsProps) {
  const pathname = usePathname() ?? "";
  const activeKey: TabKey = active ?? TABS.find((t) => t.match(pathname, slug))?.key ?? "overview";

  return (
    <nav
      aria-label="App sections"
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
  );
}
