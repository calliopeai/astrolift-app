"use client";

import type * as React from "react";

import { TOPOLOGY_META, type TopologyKind } from "@/lib/topology";
import { APP_VIEWS, useMotion, useVizPrefs, type AppView as AppStyle } from "@/lib/viz-prefs";

import { APP_GRAPH_LEGEND, AppGraph } from "./app/AppGraph";
import { APP_ISOMETRIC_LEGEND, AppIsometric } from "./app/AppIsometric";
import { AGENT_LAYOUT_LEGEND, AgentLayout } from "./app/layouts/AgentLayout";
import { FUNCTIONS_LAYOUT_LEGEND, FunctionsLayout } from "./app/layouts/FunctionsLayout";
import { JOBS_LAYOUT_LEGEND, JobsLayout, type JobRun } from "./app/layouts/JobsLayout";
import {
  MICROSERVICES_LAYOUT_LEGEND,
  MicroservicesLayout,
} from "./app/layouts/MicroservicesLayout";
import { SERVICE_AGENT_LAYOUT_LEGEND, ServiceAgentLayout } from "./app/layouts/ServiceAgentLayout";
import { SERVICE_DATA_LAYOUT_LEGEND, ServiceDataLayout } from "./app/layouts/ServiceDataLayout";
import { SERVICE_LAYOUT_LEGEND, ServiceLayout } from "./app/layouts/ServiceLayout";
import {
  SERVICE_WORKER_LAYOUT_LEGEND,
  ServiceWorkerLayout,
} from "./app/layouts/ServiceWorkerLayout";
import type { AppSnapshot, AppViewProps } from "./core/app-model";
import { VizFrame } from "./core/VizFrame";
import type { LegendItem } from "./core/VizLegend";

type LayoutProps = AppViewProps & { diagram: React.ReactNode; runs?: JobRun[] };

/** The auto (and classic) layout for each topology, with its legend. */
export const APP_LAYOUTS: Record<
  TopologyKind,
  { Component: React.ComponentType<LayoutProps>; legend: LegendItem[] }
> = {
  service: { Component: ServiceLayout, legend: SERVICE_LAYOUT_LEGEND },
  "service-data": { Component: ServiceDataLayout, legend: SERVICE_DATA_LAYOUT_LEGEND },
  "service-worker": { Component: ServiceWorkerLayout, legend: SERVICE_WORKER_LAYOUT_LEGEND },
  microservices: { Component: MicroservicesLayout, legend: MICROSERVICES_LAYOUT_LEGEND },
  "service-agent": { Component: ServiceAgentLayout, legend: SERVICE_AGENT_LAYOUT_LEGEND },
  agent: { Component: AgentLayout, legend: AGENT_LAYOUT_LEGEND },
  functions: { Component: FunctionsLayout, legend: FUNCTIONS_LAYOUT_LEGEND },
  scheduled: { Component: JobsLayout, legend: JOBS_LAYOUT_LEGEND },
  task: { Component: JobsLayout, legend: JOBS_LAYOUT_LEGEND },
  workflow: { Component: JobsLayout, legend: JOBS_LAYOUT_LEGEND },
  mixed: { Component: JobsLayout, legend: JOBS_LAYOUT_LEGEND },
};

/** One legend from several; the frame keys items by label, so repeats drop. */
function mergeLegends(...lists: LegendItem[][]): LegendItem[] {
  const seen = new Set<string>();
  return lists.flat().filter((item) => !seen.has(item.label) && seen.add(item.label));
}

/** What an app style draws, and its legend. */
export function appRendering(
  style: AppStyle,
  props: AppViewProps & { runs?: JobRun[] }
): { node: React.ReactNode; legend: LegendItem[] } {
  const { runs, ...view } = props;
  const layout = APP_LAYOUTS[view.snapshot.topology];
  const Layout = layout.Component;
  // Only the jobs layout reads run history.
  const extra = Layout === JobsLayout ? { runs } : {};
  switch (style) {
    case "isometric":
      return { node: <AppIsometric {...view} />, legend: APP_ISOMETRIC_LEGEND };
    case "graph":
      return { node: <AppGraph {...view} />, legend: APP_GRAPH_LEGEND };
    case "classic":
      return { node: <Layout {...view} {...extra} diagram={null} />, legend: layout.legend };
    case "auto":
      return {
        node: <Layout {...view} {...extra} diagram={<AppIsometric {...view} />} />,
        legend: mergeLegends(layout.legend, APP_ISOMETRIC_LEGEND),
      };
  }
}

export interface AppViewFrameProps {
  snapshot: AppSnapshot;
  onSelectNode?: (nodeId: string) => void;
  /** Past runs, for the scheduled layout's history strip. */
  runs?: JobRun[];
  title?: React.ReactNode;
  description?: React.ReactNode;
  /** Controlled style; defaults to the person's saved app view. */
  value?: AppStyle;
  /** Called on a switch; defaults to saving the choice as the person's app view. */
  onChange?: (style: AppStyle) => void;
  /** Forced motion; defaults to the preference resolved against the OS. */
  motion?: AppViewProps["motion"];
  className?: string;
}

/** An app dashboard in the person's chosen style, with the switcher and its legend. */
export function AppView({
  snapshot,
  onSelectNode,
  runs,
  title,
  description,
  value,
  onChange,
  motion,
  className,
}: AppViewFrameProps) {
  const [prefs, update] = useVizPrefs();
  const resolved = useMotion();
  const style = value ?? prefs.appView;
  const m = motion ?? resolved;
  const { node, legend } = appRendering(style, {
    snapshot,
    motion: m,
    flowParticles: prefs.flowParticles,
    onSelectNode,
    runs,
  });
  return (
    <VizFrame
      title={title ?? snapshot.app.name}
      description={description ?? TOPOLOGY_META[snapshot.topology].label}
      styles={APP_VIEWS}
      value={style}
      onChange={onChange ?? ((appView) => update({ appView }))}
      motion={m}
      legend={legend}
      className={className}
    >
      <div className="p-3">{node}</div>
    </VizFrame>
  );
}
