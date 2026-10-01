"use client";

/**
 * My apps (spec 44 §4.3, decision 18): the viewer's apps with their latest
 * deploy, as a ListSummary to the Apps list's Mine view. While apps record
 * no owner, Mine is the apps the viewer holds a role on, and the panel says
 * so under its title, in the Apps list's own words.
 */

import { BoxIcon } from "lucide-react";

import { ListSummary } from "@/components/list/ListSummary";
import { StatusDot } from "@/components/StatusDot";

import { homePanelTitle, type HomePanelProps } from "../registry";
import { useHomePresentation } from "./use-home-presentation";
import { appHref, deployDot } from "./apps-agents-model";
import type { HomeRead } from "./home-reads";
import { useMyApps } from "./use-my-apps";

export interface MyAppItem {
  slug: string;
  name: string;
  /** The latest deploy's status, or null when it never deployed. */
  status: string | null;
  environment: string | null;
  /** ISO time of the latest deploy. */
  deployedAt: string | null;
}

export interface MyAppsPanelViewProps extends HomeRead {
  panel: HomePanelProps["panel"];
  items: MyAppItem[];
  count: number | null;
  /** What Mine covers while it is a stand-in; shown under the title. */
  mineNote?: string;
}

function MyAppLine({ item }: { item: MyAppItem }) {
  const { t, age, status } = useHomePresentation(true);
  return (
    <span className="flex min-w-0 items-center gap-3">
      <StatusDot status={deployDot(item.status)} />
      <span className="min-w-0 flex-1">
        <span className="block truncate font-medium" title={item.name}>
          {item.name}
        </span>
        <span className="text-muted-foreground block truncate font-mono text-xs" title={item.slug}>
          {item.slug}
        </span>
      </span>
      <span className="text-muted-foreground hidden min-w-0 shrink text-right text-xs sm:block">
        <span className="block truncate">
          {item.status ? status(item.status) : t("copy.neverDeployed")}
        </span>
        {item.environment && (
          <span className="block truncate font-mono" title={item.environment}>
            {item.environment}
          </span>
        )}
      </span>
      <span className="text-muted-foreground w-16 shrink-0 text-right font-mono text-xs">
        {item.deployedAt ? age(item.deployedAt) : ""}
      </span>
    </span>
  );
}

/** Pure. */
export function MyAppsPanelView({
  panel,
  items,
  count,
  mineNote,
  loading,
  error,
  onRetry,
}: MyAppsPanelViewProps) {
  const { t } = useHomePresentation();
  return (
    <ListSummary
      title={homePanelTitle(panel, t)}
      icon={<BoxIcon className="size-4" />}
      description={mineNote}
      span={panel.span}
      count={count}
      rows={items}
      keyOf={(a) => a.slug}
      renderRow={(a) => <MyAppLine item={a} />}
      rowHref={(a) => appHref(a.slug)}
      viewAllHref={panel.href}
      loading={loading}
      error={error}
      onRetry={onRetry}
      empty={{
        icon: <BoxIcon />,
        title: t("copy.noAppsTitle"),
        description: t("copy.noAppsDescription"),
        actionHref: "/apps",
        actionLabel: t("copy.browseApps"),
      }}
    />
  );
}

/** Registered on Home as `my-apps`. */
export function MyAppsPanel({ panel }: HomePanelProps) {
  return <MyAppsPanelView panel={panel} {...useMyApps()} />;
}
