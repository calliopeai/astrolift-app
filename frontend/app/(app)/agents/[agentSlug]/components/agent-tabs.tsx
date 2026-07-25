"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/utils";

import { AGENT_PILLARS, agentBasePath, resolveActivePillar } from "./agent-nav-model";

/**
 * Per-agent BROCS pillar bar — the primary nav for `/agents/[agentSlug]`, the
 * agent-detail clone of `AppTabs`
 * (`app/(app)/apps/[slug]/components/app-tabs.tsx`).
 *
 * Each pillar links to its agent-native page at `/agents/[agentSlug]/<segment>`.
 * The active pillar is resolved from the pathname via the shared nav model
 * ({@link resolveActivePillar}), which also maps the platform sub-pages
 * (config, deployments, …) onto their owning pillar — so a platform page like
 * `…/config` still lights up Build here, and `AppPlatformLinks` renders Build's
 * sub-row beneath it. (The app surface folds both nav levels into one component;
 * the agent surface splits them because its pillar links target agent-native
 * pages, not the platform sub-pages.)
 *
 * Labels are inline strings (not i18n) to match the rest of the agents area
 * (`agents-client.tsx`, skills, tools), which is built without the `next-intl`
 * namespaces the apps surface uses.
 */

interface AgentTabsProps {
  agentSlug: string;
}

export function AgentTabs({ agentSlug }: AgentTabsProps) {
  const pathname = usePathname() ?? "";
  const activePillar = resolveActivePillar(pathname, agentSlug);
  const base = agentBasePath(agentSlug);

  return (
    <div className="-mx-6">
      <nav
        aria-label="Agent pillars"
        className="border-border flex gap-1 overflow-x-auto border-b px-6 scrollbar-none [mask-image:linear-gradient(to_right,transparent_0,black_1.5rem,black_calc(100%-3rem),transparent_100%)]"
      >
        {AGENT_PILLARS.map((pillar) => {
          const active = pillar.key === activePillar;
          return (
            <Link
              key={pillar.key}
              href={`${base}/${pillar.segment}`}
              aria-current={active ? "page" : undefined}
              className={cn(
                "relative shrink-0 px-3 py-2.5 text-sm font-medium transition-colors",
                active ? "text-foreground" : "text-muted-foreground hover:text-foreground"
              )}
            >
              {pillar.label}
              {active && (
                <span className="absolute inset-x-1 -bottom-px h-0.5 rounded-full bg-[var(--brand-primary)]" />
              )}
            </Link>
          );
        })}
      </nav>
    </div>
  );
}
