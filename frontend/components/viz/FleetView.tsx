"use client";

import type * as React from "react";

import { FLEET_VIEWS, useMotion, useVizPrefs, type FleetView as FleetStyle } from "@/lib/viz-prefs";

import type { FleetSnapshot, FleetViewProps } from "./core/fleet-model";
import { VizFrame } from "./core/VizFrame";
import type { LegendItem } from "./core/VizLegend";
import { FLEET_GRAPH_LEGEND, FleetGraph } from "./fleet/FleetGraph";
import { FLEET_SWARM_LEGEND, FleetSwarm } from "./fleet/FleetSwarm";
import { FLEET_HEARTBEAT_LEGEND, FleetHeartbeat } from "./fleet/FleetHeartbeat";
import { FLEET_HIVE_LEGEND, FleetHive } from "./fleet/FleetHive";
import { FLEET_ISOMETRIC_LEGEND, FleetIsometric } from "./fleet/FleetIsometric";
import { FLEET_LIST_LEGEND, FleetList } from "./fleet/FleetList";
import { FLEET_MANIFEST_LEGEND, FleetManifest } from "./fleet/FleetManifest";
import { FLEET_ORBIT_LEGEND, FleetOrbit } from "./fleet/FleetOrbit";

/** Each fleet style's renderer and the legend saying what it draws. */
export const FLEET_RENDERERS: Record<
  FleetStyle,
  { Component: React.ComponentType<FleetViewProps>; legend: LegendItem[] }
> = {
  orbit: { Component: FleetOrbit, legend: FLEET_ORBIT_LEGEND },
  heartbeat: { Component: FleetHeartbeat, legend: FLEET_HEARTBEAT_LEGEND },
  hive: { Component: FleetHive, legend: FLEET_HIVE_LEGEND },
  manifest: { Component: FleetManifest, legend: FLEET_MANIFEST_LEGEND },
  isometric: { Component: FleetIsometric, legend: FLEET_ISOMETRIC_LEGEND },
  graph: { Component: FleetGraph, legend: FLEET_GRAPH_LEGEND },
  swarm: { Component: FleetSwarm, legend: FLEET_SWARM_LEGEND },
  list: { Component: FleetList, legend: FLEET_LIST_LEGEND },
};

export interface FleetViewFrameProps {
  snapshot: FleetSnapshot;
  onSelectAgent?: (agentId: string) => void;
  selectedAgentId?: string;
  title?: React.ReactNode;
  description?: React.ReactNode;
  /** Controlled style; defaults to the person's saved fleet view. */
  value?: FleetStyle;
  /** Called on a switch; defaults to saving the choice as the person's fleet view. */
  onChange?: (style: FleetStyle) => void;
  /** Forced motion; defaults to the preference resolved against the OS. */
  motion?: FleetViewProps["motion"];
  className?: string;
}

/** The fleet in the person's chosen style, with the switcher and that style's legend. */
export function FleetView({
  snapshot,
  onSelectAgent,
  selectedAgentId,
  title = "Agent fleet",
  description,
  value,
  onChange,
  motion,
  className,
}: FleetViewFrameProps) {
  const [prefs, update] = useVizPrefs();
  const resolved = useMotion();
  const style = value ?? prefs.fleetView;
  const m = motion ?? resolved;
  const { Component, legend } = FLEET_RENDERERS[style];
  return (
    <VizFrame
      title={title}
      description={
        description ??
        `${snapshot.agents.length} agents across ${snapshot.clusters.length} clusters`
      }
      styles={FLEET_VIEWS}
      value={style}
      onChange={onChange ?? ((fleetView) => update({ fleetView }))}
      motion={m}
      legend={legend}
      className={className}
    >
      <div className="p-3">
        <Component
          snapshot={snapshot}
          motion={m}
          onSelectAgent={onSelectAgent}
          selectedAgentId={selectedAgentId}
        />
      </div>
    </VizFrame>
  );
}
