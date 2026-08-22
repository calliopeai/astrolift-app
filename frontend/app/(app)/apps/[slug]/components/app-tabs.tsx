"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useTranslations } from "next-intl";

import { cn } from "@/lib/utils";

import { useAppChrome } from "./app-chrome-context";

/**
 * Public override key. Callers pass `active` as one of these legacy tab
 * keys; we resolve each to its owning pillar + sub-tab so existing pages
 * keep working without edits. Inferring from the pathname is preferred —
 * `active` is only an explicit fallback when a page knows its own section.
 */
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

type PillarKey = "build" | "run" | "observe" | "control" | "secure";

/** A leaf sub-tab → a real flat route at `<base>/[slug]/<sub>`. */
interface SubTab {
  /** i18n key under `apps.tabs.*`. */
  label: string;
  href: (base: string, slug: string) => string;
  /** True when this sub-tab owns the current pathname. */
  match: (base: string, pathname: string, slug: string) => boolean;
  /** Legacy `active` keys that should resolve to this sub-tab. */
  legacy: TabKey[];
}

interface Pillar {
  key: PillarKey;
  /** i18n key under `apps.tabs.pillars.*`. */
  label: string;
  subs: SubTab[];
}

const at = (base: string, slug: string, sub: string) => `${base}/${slug}/${sub}`;
const under = (sub: string) => (base: string, p: string, s: string) =>
  p === at(base, s, sub) || p.startsWith(`${at(base, s, sub)}/`);

/**
 * The App detail's twelve+ flat routes folded into the five BROCS pillars
 * (Build · Run · Observe · Control · Secure). Routes stay flat at
 * `<base>/[slug]/<sub>`; this only adds a two-level tab identity over them.
 * `<base>` is `/apps` normally, `/agents` when the app is an agent (chrome
 * context). Every existing sub-route lands in exactly one pillar.
 */
const PILLARS: Pillar[] = [
  {
    key: "build",
    label: "build",
    subs: [
      { label: "config", href: (b, s) => at(b, s, "config"), match: under("config"), legacy: [] },
      {
        label: "manifest",
        href: (b, s) => at(b, s, "manifest"),
        match: under("manifest"),
        legacy: [],
      },
      {
        label: "webhooks",
        href: (b, s) => at(b, s, "webhooks"),
        match: under("webhooks"),
        legacy: [],
      },
    ],
  },
  {
    key: "run",
    label: "run",
    subs: [
      {
        label: "overview",
        href: (b, s) => `${b}/${s}`,
        match: (b, p, s) => p === `${b}/${s}`,
        legacy: ["overview"],
      },
      {
        label: "deployments",
        href: (b, s) => at(b, s, "deployments"),
        match: under("deployments"),
        legacy: ["deployments"],
      },
      {
        label: "environments",
        href: (b, s) => at(b, s, "environments"),
        match: under("environments"),
        legacy: [],
      },
      { label: "jobs", href: (b, s) => at(b, s, "jobs"), match: under("jobs"), legacy: [] },
      {
        label: "workloads",
        href: (b, s) => at(b, s, "workloads"),
        match: under("workloads"),
        legacy: ["workloads"],
      },
      {
        label: "topology",
        href: (b, s) => at(b, s, "topology"),
        match: under("topology"),
        legacy: ["topology"],
      },
      {
        label: "previews",
        href: (b, s) => at(b, s, "previews"),
        match: under("previews"),
        legacy: ["previews"],
      },
    ],
  },
  {
    key: "observe",
    label: "observe",
    subs: [
      {
        label: "observability",
        href: (b, s) => at(b, s, "observability"),
        match: under("observability"),
        legacy: ["observability"],
      },
      {
        // Formerly "console", which also carried the shell and the script
        // upload. Those moved to Control › Shell (#1247); what stayed is the
        // log stream, and `legacy` keeps old `/console` links resolving here.
        label: "logs",
        href: (b, s) => at(b, s, "logs"),
        match: under("logs"),
        legacy: ["console"],
      },
    ],
  },
  {
    key: "control",
    label: "control",
    subs: [
      {
        // Acting on a running pod is control, not observability: an
        // interactive root shell and an arbitrary one-off command belong
        // beside the other levers, not beside the log tail (#1247).
        label: "shell",
        href: (b, s) => at(b, s, "shell"),
        match: under("shell"),
        legacy: [],
      },
      {
        label: "commands",
        href: (b, s) => at(b, s, "commands"),
        match: under("commands"),
        legacy: [],
      },
      {
        label: "domains",
        href: (b, s) => at(b, s, "domains"),
        match: under("domains"),
        legacy: ["domains"],
      },
      {
        label: "managedServices",
        href: (b, s) => at(b, s, "managed-services"),
        match: under("managed-services"),
        legacy: [],
      },
      {
        label: "settings",
        href: (b, s) => at(b, s, "settings"),
        match: under("settings"),
        legacy: ["settings"],
      },
      {
        label: "members",
        href: (b, s) => at(b, s, "members"),
        match: under("members"),
        legacy: ["members"],
      },
    ],
  },
  {
    key: "secure",
    label: "secure",
    subs: [
      {
        label: "security",
        href: (b, s) => at(b, s, "security"),
        match: under("security"),
        legacy: ["security"],
      },
      {
        label: "secrets",
        href: (b, s) => at(b, s, "secrets"),
        match: under("secrets"),
        legacy: ["secrets"],
      },
      { label: "tokens", href: (b, s) => at(b, s, "tokens"), match: under("tokens"), legacy: [] },
    ],
  },
];

/** Resolve the active pillar + sub-tab from the pathname, falling back to
 * an explicit `active` key, and finally to Run › Overview (the default
 * landing for `/apps/[slug]`). */
function resolveActive(
  base: string,
  pathname: string,
  slug: string,
  active?: TabKey
): { pillar: PillarKey; sub: string } {
  // 1. Pathname is the source of truth — keeps deep links correct.
  for (const pillar of PILLARS) {
    for (const sub of pillar.subs) {
      if (sub.match(base, pathname, slug)) return { pillar: pillar.key, sub: sub.label };
    }
  }
  // 2. Explicit override from a page that knows its own section.
  if (active) {
    for (const pillar of PILLARS) {
      for (const sub of pillar.subs) {
        if (sub.legacy.includes(active)) return { pillar: pillar.key, sub: sub.label };
      }
    }
  }
  // 3. Default landing.
  return { pillar: "run", sub: "overview" };
}

interface AppTabsProps {
  slug: string;
  /** Explicit override for the active section; otherwise inferred from pathname. */
  active?: TabKey;
}

/**
 * Two-level link nav for `/apps/[slug]`: a primary BROCS pillar bar and a
 * secondary sub-tab row scoped to the active pillar. Each sub-tab is a real
 * flat route — the sibling subroutes (`environments/`, `domains/`, …) still
 * own their pages; this only gives the page a tabbed top-level identity.
 */
export function AppTabs({ slug, active }: AppTabsProps) {
  const { basePath, agentShell } = useAppChrome();
  const t = useTranslations("apps.tabs");
  const pathname = usePathname() ?? "";

  // In the agent shell the AgentDetailShell already renders the agent's BROCS
  // pillar bar + the app-platform-links row; a second app-style tab bar would
  // duplicate chrome. Suppress it — this is the single mechanism that hides
  // AppTabs across all 13 shared clients without editing each one.
  if (agentShell) return null;

  const { pillar: activePillar, sub: activeSub } = resolveActive(basePath, pathname, slug, active);

  const current = PILLARS.find((p) => p.key === activePillar) ?? PILLARS[1];

  return (
    <div className="-mx-6">
      {/* Primary: BROCS pillars as a segmented mode-switcher. Contained
          chips read as "which layer am I in", categorically distinct from
          the page-level underline tabs below — two stacked underline rows
          were indistinguishable at a glance. Mono uppercase because BROCS
          is the brand vocabulary, not a page name. */}
      <nav
        aria-label={t("pillarAriaLabel")}
        className="scrollbar-none flex gap-1 overflow-x-auto [mask-image:linear-gradient(to_right,transparent_0,black_1.5rem,black_calc(100%-3rem),transparent_100%)] px-6 pt-1 pb-2"
      >
        <div className="bg-muted/40 border-border flex shrink-0 gap-0.5 rounded-sm border p-0.5">
          {PILLARS.map((pillar) => {
            const isActive = pillar.key === activePillar;
            // Land on the pillar's first sub-tab; Run leads with Overview.
            const href = pillar.subs[0].href(basePath, slug);
            const label = t(`pillars.${pillar.label}`);
            return (
              <Link
                key={pillar.key}
                href={href}
                aria-current={isActive ? "page" : undefined}
                className={cn(
                  "shrink-0 rounded-sm px-3.5 py-1.5 font-mono text-sm font-medium tracking-wider uppercase transition-colors",
                  isActive
                    ? "text-foreground bg-[var(--brand-primary)]/12 shadow-[inset_0_0_0_1px_var(--brand-primary)]"
                    : "text-muted-foreground hover:text-foreground hover:bg-muted/60"
                )}
              >
                {/* The initial carries the brand: the five of them spell
                    BROCS. Split inside one element so the accessible name is
                    still the whole word. */}
                <span className="text-[var(--brand-primary)]">{label.charAt(0)}</span>
                {label.slice(1)}
              </Link>
            );
          })}
        </div>
      </nav>

      {/* Secondary: pages of the active pillar as quiet underline tabs.
          Always rendered, even when a pillar has a single sub-route, so
          the two-level rhythm stays stable across pillars. */}
      <nav
        aria-label={t("ariaLabel")}
        className="border-border scrollbar-none flex gap-1 overflow-x-auto border-b [mask-image:linear-gradient(to_right,transparent_0,black_1.5rem,black_calc(100%-3rem),transparent_100%)] px-6"
      >
        {current.subs.map((sub) => {
          const isActive = sub.label === activeSub;
          return (
            <Link
              key={sub.label}
              href={sub.href(basePath, slug)}
              aria-current={isActive ? "page" : undefined}
              className={cn(
                "relative shrink-0 px-3 py-2 text-sm transition-colors",
                isActive
                  ? "text-foreground font-medium"
                  : "text-muted-foreground hover:text-foreground"
              )}
            >
              {t(sub.label)}
              {isActive && (
                <span className="absolute inset-x-1 -bottom-px h-0.5 rounded-full bg-[var(--brand-primary)]" />
              )}
            </Link>
          );
        })}
      </nav>
    </div>
  );
}
