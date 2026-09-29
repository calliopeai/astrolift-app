"use client";

import { AGENTS_LIST } from "@/components/screens/agents/list/agents-list";
import type { AstroliftAgentListItem } from "@/graphql/agents/agents.types";

import { combineReads, useHomeFleet, useHomeMyApps } from "./home-reads";
import type { MyAgentItem, MyAgentsPanelViewProps } from "./MyAgentsPanel";

/** The Agents list's own note for Mine, so the panel and the list say the same. */
export const MY_AGENTS_NOTE = AGENTS_LIST.views.find((v) => v.key === "mine")?.note;

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
 * two reads (and variables) as the Agents list's Mine view, so opening that
 * list from View all is a cache hit.
 */
export function useMyAgents(): Omit<MyAgentsPanelViewProps, "panel"> {
  const fleet = useHomeFleet();
  const myApps = useHomeMyApps("all");
  const slugs = new Set(myApps.apps.map((a) => a.slug));
  const mine = fleet.agents.filter((a) => slugs.has(a.appSlug)).map(myAgentItem);
  const read = combineReads([fleet, myApps], false);
  const answered = !read.loading && !read.error;
  return {
    items: answered ? mine : [],
    count: answered ? mine.length : null,
    mineNote: MY_AGENTS_NOTE,
    ...read,
  };
}
