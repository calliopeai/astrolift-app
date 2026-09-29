"use client";

import { APPS_LIST } from "@/components/screens/apps/list/apps-list";
import type { AstroliftRegisteredApp } from "@/graphql/registry/registry.types";

import { useHomeMyApps } from "./home-reads";
import type { MyAppItem, MyAppsPanelViewProps } from "./MyAppsPanel";

/** The Apps list's own note for Mine, so the panel and the list say the same. */
export const MY_APPS_NOTE = APPS_LIST.views.find((v) => v.key === "mine")?.note;

export function myAppItem(a: AstroliftRegisteredApp): MyAppItem {
  const latest = a.latestDeployment;
  return {
    slug: a.slug,
    name: a.name || a.slug,
    status: latest?.status ?? null,
    environment: latest?.environmentName || null,
    deployedAt: latest?.startedAt ?? latest?.createdAt ?? a.lastDeployedAt ?? null,
  };
}

/** My apps' data: five of the viewer's apps with their latest deploy, and the total. */
export function useMyApps(): Omit<MyAppsPanelViewProps, "panel"> {
  const { apps, total, ...read } = useHomeMyApps("top");
  return { items: apps.map(myAppItem), count: total, mineNote: MY_APPS_NOTE, ...read };
}
