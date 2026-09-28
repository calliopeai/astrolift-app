"use client";

import { DetailTabRow } from "@/components/DetailPageTabs";

import { AGENT_PILLARS, agentBasePath, resolveActivePillar } from "./agent-nav-model";

/**
 * Per-agent BROCS pillar bar — the primary nav for `/agents/[agentSlug]`, the
 * agent-detail clone of `AppTabsView` (`components/screens/apps/detail/AppTabs`).
 *
 * Each pillar links to its agent-native page at `/agents/[agentSlug]/<segment>`.
 * The active pillar is resolved from the pathname via the shared nav model
 * ({@link resolveActivePillar}), which also maps the platform sub-pages
 * (config, deployments, …) onto their owning pillar — so a platform page like
 * `…/config` still lights up Build here, and `AppPlatformLinksView` renders
 * Build's sub-row beneath it.
 *
 * Labels are inline strings (not i18n) to match the rest of the agents area,
 * which is built without the `next-intl` namespaces the apps surface uses.
 */
export interface AgentTabsViewProps {
  agentSlug: string;
  /** The current pathname; the active pillar is resolved from it. */
  pathname: string;
}

export function AgentTabsView({ agentSlug, pathname }: AgentTabsViewProps) {
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
