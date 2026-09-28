"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import * as React from "react";

import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import { FEATURE_FLAG_ZENTINELLE, useFeatureFlag } from "@/graphql/server/server.hooks";

import { AGENT_TABS, type AgentTab, ZENTINELLE_TABS } from "./agents-list-tabs";

/**
 * The Agents fleet page's tab state (URL-synced via ?tab=), the Zentinelle
 * gate on the governance tabs, and the active org every tab queries under.
 * The data half of AgentsScreen.
 */
export function useAgentsScreen() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  // Zentinelle governance surfaces (Activity / Reasoning / Token-Usage /
  // Compliance) ship in the codebase but stay hidden unless the install
  // enables them via the `zentinelle.enabled` server-info flag (#1104).
  const zentinelleEnabled = useFeatureFlag(FEATURE_FLAG_ZENTINELLE);
  const visibleTabs = React.useMemo(
    () =>
      zentinelleEnabled ? AGENT_TABS : AGENT_TABS.filter((tabKey) => !ZENTINELLE_TABS.has(tabKey)),
    [zentinelleEnabled]
  );

  const rawTab = searchParams.get("tab") as AgentTab | null;
  // A ?tab=compliance deep-link while Zentinelle is disabled falls back
  // to the default tab rather than rendering a dead / gated surface.
  const tab: AgentTab = rawTab && visibleTabs.includes(rawTab) ? rawTab : "active";

  function onTabChange(next: AgentTab) {
    const params = new URLSearchParams(searchParams.toString());
    if (next === "active") {
      params.delete("tab");
    } else {
      params.set("tab", next);
    }
    const qs = params.toString();
    router.replace(`${pathname}${qs ? `?${qs}` : ""}`, { scroll: false });
  }

  // Resolve the active org reactively (not a one-shot cookie read): the
  // cookie is set by useActiveOrg's post-render effect after the org query
  // resolves, so reading it synchronously at first render races and returns
  // "" on a fresh load — leaving every tab's query skipped (skip: !orgId)
  // and the page empty with no re-render to recover. useActiveOrg re-renders
  // when the org loads, so orgId becomes populated and the queries fire.
  const { org } = useActiveOrg();
  const orgId = org?.id ?? "";

  return { visibleTabs, tab, onTabChange, orgId };
}
