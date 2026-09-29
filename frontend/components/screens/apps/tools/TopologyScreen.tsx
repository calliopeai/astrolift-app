"use client";

import { BoxIcon, NetworkIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";

import { Panel, type PanelSpan } from "@/components/panel/Panel";
import type { TopologyEdge, TopologyNode } from "@/components/topology/types";
import { AppView } from "@/components/viz/AppView";
import { Skeleton } from "@/components/ui/skeleton";
import type { AstroliftRegisteredApp } from "@/graphql/registry/registry.types";
import { TOPOLOGY_META } from "@/lib/topology";
import { cn } from "@/lib/utils";

import { topologySnapshot } from "./topology-snapshot";

export interface TopologyScreenProps {
  slug: string;
  app: Pick<AstroliftRegisteredApp, "name" | "slug"> | null;
  loading: boolean;
  workloadsLoading: boolean;
  nodes: TopologyNode[];
  edges: TopologyEdge[];
  /** Where the empty state's "add a workload" action goes (chrome-aware). */
  workloadsHref: string;
  /** A node was picked: the route opens its workload, service or domain. */
  onSelectNode?: (node: TopologyNode) => void;
  span?: PanelSpan;
}

/**
 * The overview's topology panel (spec 44 §5.2; the former Topology tab,
 * #705). The app's workloads, ingress and managed services drawn by AppView
 * in the person's chosen view style, with the style picker and the legend
 * for what colour and motion mean. The picture carries health and wiring
 * only; see topology-snapshot for why traffic reads zero.
 */
export function TopologyScreen({
  slug,
  app: a,
  loading,
  workloadsLoading,
  nodes,
  edges,
  workloadsHref,
  onSelectNode,
  span = 12,
}: TopologyScreenProps) {
  const t = useTranslations("apps.topology");
  const tDetail = useTranslations("apps.detail.topology");
  const snapshot = React.useMemo(
    () => topologySnapshot({ slug: a?.slug ?? slug, name: a?.name ?? slug }, nodes, edges),
    [a?.slug, a?.name, slug, nodes, edges]
  );

  if (loading || workloadsLoading || !a || nodes.length === 0) {
    return (
      <Panel
        title={tDetail("title")}
        icon={<NetworkIcon className="size-4" />}
        span={span}
        loading={loading || workloadsLoading}
        skeleton={<Skeleton className="h-64 w-full" />}
        empty={{
          icon: <BoxIcon className="size-5" />,
          title: t("emptyTitle"),
          description: t("emptyDescription"),
          actionHref: workloadsHref,
          actionLabel: t("emptyAction"),
        }}
      />
    );
  }

  const byId = new Map(nodes.map((n) => [n.id, n]));
  return (
    <div className={cn("col-span-12 min-w-0", SPAN_CLASS[span])}>
      <AppView
        snapshot={snapshot}
        title={<span className="[overflow-wrap:anywhere]">{tDetail("title")}</span>}
        description={
          <span className="[overflow-wrap:anywhere]">
            {TOPOLOGY_META[snapshot.topology].label} · {t("graphDescription")}
          </span>
        }
        onSelectNode={
          onSelectNode
            ? (id) => {
                const node = byId.get(id);
                if (node) onSelectNode(node);
              }
            : undefined
        }
      />
    </div>
  );
}

/** Matches Panel's spans, for the AppView frame that stands in for one. */
const SPAN_CLASS: Record<PanelSpan, string> = {
  12: "",
  9: "xl:col-span-9",
  8: "xl:col-span-8",
  6: "xl:col-span-6",
  4: "xl:col-span-4",
  3: "lg:col-span-6 xl:col-span-3",
};
