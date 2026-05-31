"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useTranslations } from "next-intl";

import { cn } from "@/lib/utils";

type TabKey =
  | "overview"
  | "deployments"
  | "workloads"
  | "topology"
  | "observability"
  | "console"
  | "previews"
  | "domains"
  | "secrets"
  | "security"
  | "members"
  | "settings";

interface TabSpec {
  key: TabKey;
  href: (slug: string) => string;
  /** Sub-paths that should still highlight this tab when matched. */
  match: (pathname: string, slug: string) => boolean;
}

const TABS: TabSpec[] = [
  {
    key: "overview",
    href: (s) => `/apps/${s}`,
    match: (p, s) => p === `/apps/${s}`,
  },
  {
    key: "deployments",
    href: (s) => `/apps/${s}/deployments`,
    match: (p, s) =>
      p === `/apps/${s}/deployments` ||
      p.startsWith(`/apps/${s}/deployments/`) ||
      p.startsWith(`/apps/${s}/environments`) ||
      p.startsWith(`/apps/${s}/jobs`) ||
      p.startsWith(`/apps/${s}/commands`),
  },
  {
    key: "workloads",
    href: (s) => `/apps/${s}/workloads`,
    match: (p, s) => p.startsWith(`/apps/${s}/workloads`),
  },
  {
    key: "topology",
    href: (s) => `/apps/${s}/topology`,
    match: (p, s) => p.startsWith(`/apps/${s}/topology`),
  },
  {
    key: "observability",
    href: (s) => `/apps/${s}/observability`,
    match: (p, s) => p.startsWith(`/apps/${s}/observability`),
  },
  {
    key: "console",
    href: (s) => `/apps/${s}/console`,
    match: (p, s) => p.startsWith(`/apps/${s}/console`),
  },
  {
    key: "previews",
    href: (s) => `/apps/${s}/previews`,
    match: (p, s) => p.startsWith(`/apps/${s}/previews`),
  },
  {
    key: "domains",
    href: (s) => `/apps/${s}/domains`,
    match: (p, s) => p.startsWith(`/apps/${s}/domains`),
  },
  {
    key: "secrets",
    href: (s) => `/apps/${s}/secrets`,
    match: (p, s) => p.startsWith(`/apps/${s}/secrets`) || p.startsWith(`/apps/${s}/tokens`),
  },
  {
    key: "security",
    href: (s) => `/apps/${s}/security`,
    match: (p, s) => p.startsWith(`/apps/${s}/security`),
  },
  {
    key: "members",
    href: (s) => `/apps/${s}/members`,
    match: (p, s) => p.startsWith(`/apps/${s}/members`),
  },
  {
    key: "settings",
    href: (s) => `/apps/${s}/settings`,
    match: (p, s) =>
      p.startsWith(`/apps/${s}/settings`) ||
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
  const t = useTranslations("apps.tabs");
  const pathname = usePathname() ?? "";
  const activeKey: TabKey = active ?? TABS.find((t) => t.match(pathname, slug))?.key ?? "overview";

  return (
    <nav
      aria-label={t("ariaLabel")}
      className="border-border -mx-6 flex gap-1 overflow-x-auto border-b px-6 scrollbar-none [mask-image:linear-gradient(to_right,transparent_0,black_1.5rem,black_calc(100%-3rem),transparent_100%)] sm:[mask-image:none]"
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
            {t(tab.key)}
            {isActive && (
              <span className="absolute inset-x-1 -bottom-px h-0.5 rounded-full bg-[var(--brand-primary)]" />
            )}
          </Link>
        );
      })}
    </nav>
  );
}
