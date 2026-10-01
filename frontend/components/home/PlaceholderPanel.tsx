"use client";

/**
 * The stand-in every Home panel renders until its real component lands (the
 * next phase moves DashboardScreen's panels into the registry). It is built
 * from the primitive the panel will be, so the page already has its final
 * shape: a list is a ListSummary, a feed is a Feed in a Panel, KPIs and
 * charts are a Panel. It fetches nothing. Pure.
 */

import { ActivityIcon, BarChart3Icon, LayoutGridIcon, ListIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import { homePanelTitle } from "./registry";

import * as React from "react";

import type { EmptyStateSpec } from "@/components/data-table";
import { Feed } from "@/components/feed/Feed";
import { ListSummary } from "@/components/list/ListSummary";
import { Panel } from "@/components/panel/Panel";

import type { HomePanelKind, HomePanelProps } from "./registry";

const ICON: Record<HomePanelKind, React.ReactNode> = {
  list: <ListIcon className="size-4" />,
  feed: <ActivityIcon className="size-4" />,
  kpis: <LayoutGridIcon className="size-4" />,
  chart: <BarChart3Icon className="size-4" />,
};

const NONE: never[] = [];

export function PlaceholderPanel({ panel }: HomePanelProps) {
  const t = useTranslations("home");
  const title = homePanelTitle(panel, t);
  const empty: EmptyStateSpec = {
    icon: ICON[panel.kind],
    title: t("placeholderTitle"),
    description: t("placeholderDescription", { panel: title }),
    actionHref: panel.href,
    actionLabel: t("open"),
  };
  if (panel.kind === "list") {
    return (
      <ListSummary
        title={title}
        icon={ICON.list}
        span={panel.span}
        rows={NONE}
        keyOf={() => ""}
        renderRow={() => null}
        viewAllHref={panel.href}
        empty={empty}
      />
    );
  }
  if (panel.kind === "feed") {
    return (
      <Panel title={title} icon={ICON.feed} span={panel.span}>
        <Feed label={title} items={NONE} keyOf={() => ""} renderItem={() => null} empty={empty} />
      </Panel>
    );
  }
  return <Panel title={title} icon={ICON[panel.kind]} span={panel.span} empty={empty} />;
}
