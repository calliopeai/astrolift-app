"use client";

import { usePathname } from "next/navigation";

import { DetailTabRow } from "@/components/DetailPageTabs";

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
    <div className="-mx-6 min-w-0">
      <DetailTabRow
        ariaLabel="Agent pillars"
        tabs={AGENT_PILLARS.map((pillar) => ({
          key: pillar.key,
          label: pillar.label,
          href: `${base}/${pillar.segment}`,
          active: pillar.key === activePillar,
        }))}
      />
    </div>
  );
}
