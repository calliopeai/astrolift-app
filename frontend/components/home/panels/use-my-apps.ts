"use client";

import { useTranslations } from "next-intl";
import type { AstroliftRegisteredApp } from "@/graphql/registry/registry.types";

import { useHomeMyApps } from "./home-reads";
import type { MyAppItem, MyAppsPanelViewProps } from "./MyAppsPanel";

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
  const t = useTranslations("apps.list");
  const { apps, total, ...read } = useHomeMyApps("top");
  return { items: apps.map(myAppItem), count: total, mineNote: t("views.mineNote"), ...read };
}
