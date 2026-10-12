"use client";

import { BotIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";

import type { Column } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import { type ListDefinition, standardViews } from "@/components/list/list-state";
import { selectRows, type SelectRowsSpec } from "@/components/list/select-rows";
import { useLocalListState } from "@/components/list/use-list-state";
import { cn } from "@/lib/utils";

import type { FleetAgent, FleetViewProps } from "../core/fleet-model";
import { HEALTH_COLOR, MOTION_CLASS, type Health } from "../core/semantics";
import type { LegendItem } from "../core/VizLegend";

/**
 * The 'list' fleet style: a table of agents, one row each, in the model's
 * order (grouped by cluster) until a header sorts it. It is an embedded list
 * over the snapshot's own agents (search, health filter, sort, numbered
 * pages), with its list state kept in memory. The only motion is the
 * failing flicker on a status dot; under reduced motion the dot is still and
 * the health word beside it says the same thing.
 */

const HEALTHS: Health[] = ["ok", "degraded", "failing", "idle"];

export const FLEET_LIST: ListDefinition = {
  id: "viz.fleet-list",
  fields: [
    {
      key: "health",
      label: "Health",
      options: HEALTHS.map((h) => ({ value: h, label: h })),
    },
  ],
  searchPlaceholder: "Search agents, clusters…",
  // The snapshot's order: agents grouped by cluster.
  defaultSort: [{ key: "cluster", dir: "asc" }],
  views: standardViews({ owner: "me" }, [], {
    mineNote: "Agents belong to the fleet, not a person, so Mine is empty.",
  }),
  paging: "numbered",
  pageSizes: [25, 50, 100],
};

export const FLEET_LIST_LEGEND: LegendItem[] = [
  { glyph: "dot", color: HEALTH_COLOR.ok, labelKey: "healthy" },
  { glyph: "dot", color: HEALTH_COLOR.degraded, labelKey: "degradedLoad85" },
  { glyph: "flicker", color: HEALTH_COLOR.failing, labelKey: "flickerFailing" },
  { glyph: "dot", color: HEALTH_COLOR.idle, labelKey: "idle" },
  { glyph: "bar", color: HEALTH_COLOR.ok, labelKey: "fleetListLoadBar" },
];

function summarize(agents: FleetAgent[], t: ReturnType<typeof useTranslations<"vizRender">>) {
  const failing = agents.filter((a) => a.health === "failing").length;
  const busy = agents.filter((a) => a.health !== "idle" && a.activeRuns > 0).length;
  return t("fleetList.summary", { agents: agents.length, failing, busy });
}

function HealthCell({ health, motion }: { health: Health; motion: FleetViewProps["motion"] }) {
  const t = useTranslations("vizRender");
  return (
    <span className="inline-flex items-center gap-1.5">
      <span
        aria-hidden
        className={cn(
          "size-2 shrink-0 rounded-full",
          health === "failing" && motion === "full" && MOTION_CLASS.flicker
        )}
        style={{ background: HEALTH_COLOR[health] }}
      />
      <span className="text-xs">{t(`health.${health}`)}</span>
    </span>
  );
}

function LoadCell({ agent }: { agent: FleetAgent }) {
  const pct = Math.round(agent.load * 100);
  return (
    <span className="inline-flex items-center gap-2">
      <span aria-hidden className="bg-muted h-1.5 w-16 overflow-hidden rounded-sm">
        <span
          className="block h-full"
          style={{ width: `${pct}%`, background: HEALTH_COLOR[agent.health] }}
        />
      </span>
      <span className="font-mono text-xs tabular-nums">{pct}%</span>
    </span>
  );
}

export function FleetList({
  snapshot,
  motion,
  onSelectAgent,
  selectedAgentId,
  className,
}: FleetViewProps) {
  const clusters = React.useMemo(
    () => new Map(snapshot.clusters.map((c) => [c.id, c])),
    [snapshot.clusters]
  );
  const select = React.useMemo<SelectRowsSpec<FleetAgent>>(() => {
    const order = new Map(snapshot.agents.map((a, i) => [a.id, i]));
    return {
      filter: { owner: () => false, health: (a, v) => a.health === v },
      text: (a) => [a.name, clusters.get(a.clusterId)?.name ?? a.clusterId],
      sort: {
        name: (a) => a.name.toLowerCase(),
        health: (a) => HEALTHS.indexOf(a.health),
        load: (a) => a.load,
        runs: (a) => a.activeRuns,
        queued: (a) => a.queued,
        // The model's order, which is grouped by cluster.
        cluster: (a) => order.get(a.id) ?? 0,
      },
      id: (a) => a.id,
    };
  }, [snapshot.agents, clusters]);

  const t = useTranslations("vizRender");
  const fleetList = React.useMemo<ListDefinition>(
    () => ({
      ...FLEET_LIST,
      searchPlaceholder: t("fleetList.searchPlaceholder"),
      fields: FLEET_LIST.fields.map((field) => ({
        ...field,
        label: t("fleetList.health"),
        options: field.options?.map((o) => ({ ...o, label: t(`healthInline.${o.value}`) })),
      })),
      views: FLEET_LIST.views.map((view) => ({
        ...view,
        label: t(`fleetList.views.${view.key}`),
        note: view.note && t("fleetList.mineNote"),
      })),
    }),
    [t]
  );
  const list = useLocalListState(fleetList);
  const page = selectRows(
    snapshot.agents,
    {
      filters: list.filters,
      q: list.state.q,
      sort: list.state.sort,
      page: list.state.page,
      pageSize: list.state.pageSize,
    },
    select
  );

  const columns: Column<FleetAgent>[] = [
    {
      id: "agent",
      header: t("fleetList.columns.agent"),
      sortKey: "name",
      cell: (a) => {
        const name = (
          <span title={a.name} className="block max-w-56 truncate font-mono text-xs">
            {a.name}
          </span>
        );
        // One control per row, stretched over the row, as DataTable draws
        // for an activatable row: the whole row selects, by pointer or key.
        return onSelectAgent ? (
          <button
            type="button"
            aria-label={t("fleetList.agentLabel", {
              name: a.name,
              health: t(`healthInline.${a.health}`),
            })}
            onClick={() => onSelectAgent(a.id)}
            className="text-left after:absolute after:inset-0"
          >
            {name}
          </button>
        ) : (
          name
        );
      },
    },
    {
      id: "health",
      header: t("fleetList.columns.health"),
      sortKey: "health",
      width: "w-28",
      cell: (a) => <HealthCell health={a.health} motion={motion} />,
    },
    {
      id: "load",
      header: t("fleetList.columns.load"),
      sortKey: "load",
      width: "w-32",
      cell: (a) => <LoadCell agent={a} />,
    },
    {
      id: "runs",
      header: t("fleetList.columns.runs"),
      sortKey: "runs",
      width: "w-24",
      align: "right",
      cell: (a) => <span className="font-mono text-xs tabular-nums">{a.activeRuns}</span>,
    },
    {
      id: "queued",
      header: t("fleetList.columns.queued"),
      sortKey: "queued",
      width: "w-20",
      align: "right",
      cell: (a) => <span className="font-mono text-xs tabular-nums">{a.queued}</span>,
    },
    {
      id: "cluster",
      header: t("fleetList.columns.cluster"),
      sortKey: "cluster",
      cell: (a) => {
        const c = clusters.get(a.clusterId);
        const name = c?.name ?? a.clusterId;
        return (
          <span title={name} className="block max-w-48 truncate font-mono text-xs">
            {name}
          </span>
        );
      },
    },
  ];

  return (
    // A group, not an img: an img role would hide the table and its row
    // buttons from assistive tech.
    <div
      role="group"
      aria-label={summarize(snapshot.agents, t)}
      data-motion={motion}
      className={className}
    >
      <ListPage<FleetAgent>
        embedded
        list={list}
        label={t("fleetList.label")}
        columns={columns}
        rows={page.rows}
        getRowId={(a) => a.id}
        totalCount={page.totalCount}
        empty={{ icon: <BotIcon className="size-5" />, title: t("fleetList.empty") }}
        rowClassName={(a) =>
          cn(onSelectAgent && "relative cursor-pointer", a.id === selectedAgentId && "bg-muted") ||
          undefined
        }
      />
    </div>
  );
}
