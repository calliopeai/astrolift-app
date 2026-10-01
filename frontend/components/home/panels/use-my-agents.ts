"use client";

import { useTranslations } from "next-intl";

import type { AstroliftAgentListItem } from "@/graphql/agents/agents.types";

import { combineReads, useHomeFleet, useHomeMyApps } from "./home-reads";
import type { MyAgentItem, MyAgentsPanelViewProps } from "./MyAgentsPanel";

export function myAgentItem(a: AstroliftAgentListItem): MyAgentItem {
  return {
    slug: a.slug,
    name: a.name || a.slug,
    runningCount: a.runningCount ?? 0,
    paused: Boolean(a.runPaused),
    lastRunStatus: a.lastRunStatus ?? null,
    lastRunAt: a.lastRunAt ?? null,
  };
}

/**
 * My agents' data: the org's fleet joined with the viewer's apps, the same
 * two authorized source reads. This panel describes that role-based join
 * without claiming that the viewer registered or owns those agents.
 */
export function useMyAgents(): Omit<MyAgentsPanelViewProps, "panel"> {
  const t = useTranslations("home");
  const fleet = useHomeFleet();
  const myApps = useHomeMyApps("all");
  const slugs = new Set(myApps.apps.map((a) => a.slug));
  const mine = fleet.agents.filter((a) => slugs.has(a.appSlug)).map(myAgentItem);
  const read = combineReads([fleet, myApps], false);
  const answered = !read.loading && !read.error;
  return {
    items: answered ? mine : [],
    count: answered ? mine.length : null,
    mineNote: t("copy.agentsMineNote"),
    ...read,
  };
}
