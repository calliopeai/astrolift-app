"use client";

/**
 * Clusters (spec 44 §4.3, Operator): the fleet at a glance, compact. One
 * line of counts by health, then the five least healthy clusters with their
 * health, as a ListSummary to Admin › Clusters. Pure view; the data is
 * useClustersPanel.
 */

import { ServerIcon } from "lucide-react";

import { ListSummary } from "@/components/list/ListSummary";
import { providerLabel } from "@/components/screens/clusters/list/clusters-list";
import type { ClusterRow } from "@/components/screens/clusters/list/use-clusters-list";
import { StatusDot } from "@/components/StatusDot";

import { homePanelTitle, type HomePanelProps } from "../registry";
import { useHomePresentation } from "./use-home-presentation";
import { type ClusterHealth, clusterHealth, healthCounts } from "./builder-operator-model";
import type { HomeRead } from "./home-reads";
import { useClustersPanel } from "./use-home-panels";

export interface ClustersPanelViewProps extends HomeRead {
  panel: HomePanelProps["panel"];
  /** The whole fleet, least healthy first. */
  rows: ClusterRow[];
  count: number | null;
}

const HEALTH: Record<ClusterHealth, "ok" | "warn" | "error" | "muted"> = {
  error: "error",
  offline: "error",
  degraded: "warn",
  never_seen: "muted",
  connected: "ok",
};

const ORDER: ClusterHealth[] = ["error", "offline", "degraded", "never_seen", "connected"];

/** "3 connected · 1 offline", the healths that have any clusters, worst first. */
function Summary({ rows }: { rows: ClusterRow[] }) {
  const { t, number } = useHomePresentation();
  const counts = healthCounts(rows);
  const parts = ORDER.filter((h) => counts[h] > 0);
  return (
    <span className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
      {parts.map((h) => (
        <span key={h} className="inline-flex items-center gap-1.5">
          <StatusDot status={HEALTH[h]} />
          <span className="font-mono tabular-nums">{number(counts[h])}</span>
          <span>{t(`health.${h}`)}</span>
        </span>
      ))}
    </span>
  );
}

function ClusterLine({ cluster }: { cluster: ClusterRow }) {
  const { t } = useHomePresentation();
  const health = HEALTH[clusterHealth(cluster)];
  return (
    <span className="flex min-w-0 items-center gap-2">
      <StatusDot status={health} />
      <span className="min-w-0 flex-1 truncate" title={cluster.name}>
        {cluster.name}
      </span>
      <span className="text-muted-foreground hidden shrink-0 font-mono text-xs sm:inline">
        {providerLabel(cluster.providerPluginSlug)} · {cluster.region}
      </span>
      <span className="text-muted-foreground shrink-0 text-xs">
        {t(`health.${clusterHealth(cluster)}`)}
      </span>
    </span>
  );
}

export function ClustersPanelView({
  panel,
  rows,
  count,
  loading,
  error,
  onRetry,
}: ClustersPanelViewProps) {
  const { t } = useHomePresentation();
  return (
    <ListSummary
      title={homePanelTitle(panel, t)}
      icon={<ServerIcon className="size-4" />}
      description={rows.length > 0 ? <Summary rows={rows} /> : undefined}
      span={panel.span}
      count={loading || error ? null : count}
      rows={rows}
      keyOf={(c) => c.id}
      renderRow={(c) => <ClusterLine cluster={c} />}
      rowHref={(c) => `/clusters/${encodeURIComponent(c.slug)}`}
      viewAllHref={panel.href}
      loading={loading}
      error={error}
      onRetry={onRetry}
      empty={{
        icon: <ServerIcon />,
        title: t("copy.noClustersTitle"),
        description: t("copy.noClustersDescription"),
        actionHref: "/clusters/new",
        actionLabel: t("copy.registerCluster"),
      }}
    />
  );
}

/** Registered on Home as `clusters`. */
export function ClustersPanel({ panel }: HomePanelProps) {
  return <ClustersPanelView panel={panel} {...useClustersPanel()} />;
}
