"use client";

import type * as React from "react";

import {
  WORKFLOW_VIEWS,
  useMotion,
  useVizPrefs,
  type WorkflowView as WorkflowStyle,
} from "@/lib/viz-prefs";

import { VizFrame } from "./core/VizFrame";
import type { LegendItem } from "./core/VizLegend";
import type { WorkflowSnapshot, WorkflowViewProps } from "./core/workflow-model";
import { WORKFLOW_GRAPH_LEGEND, WorkflowGraph } from "./workflow/WorkflowGraph";
import { WORKFLOW_ISOMETRIC_LEGEND, WorkflowIsometric } from "./workflow/WorkflowIsometric";
import { WORKFLOW_LIST_LEGEND, WorkflowList } from "./workflow/WorkflowList";
import { WORKFLOW_TRANSIT_LEGEND, WorkflowTransit } from "./workflow/WorkflowTransit";

/** Each workflow style's renderer and the legend saying what it draws. */
export const WORKFLOW_RENDERERS: Record<
  WorkflowStyle,
  { Component: React.ComponentType<WorkflowViewProps>; legend: LegendItem[] }
> = {
  transit: { Component: WorkflowTransit, legend: WORKFLOW_TRANSIT_LEGEND },
  isometric: { Component: WorkflowIsometric, legend: WORKFLOW_ISOMETRIC_LEGEND },
  graph: { Component: WorkflowGraph, legend: WORKFLOW_GRAPH_LEGEND },
  list: { Component: WorkflowList, legend: WORKFLOW_LIST_LEGEND },
};

export interface WorkflowViewFrameProps {
  snapshot: WorkflowSnapshot;
  onSelectRun?: (trainId: string) => void;
  onSelectStation?: (lineId: string, stationId: string) => void;
  title?: React.ReactNode;
  description?: React.ReactNode;
  /** Controlled style; defaults to the person's saved workflow view. */
  value?: WorkflowStyle;
  /** Called on a switch; defaults to saving the choice as the person's workflow view. */
  onChange?: (style: WorkflowStyle) => void;
  /** Forced motion; defaults to the preference resolved against the OS. */
  motion?: WorkflowViewProps["motion"];
  className?: string;
}

/** Workflows in the person's chosen style, with the switcher and that style's legend. */
export function WorkflowView({
  snapshot,
  onSelectRun,
  onSelectStation,
  title = "Workflows",
  description,
  value,
  onChange,
  motion,
  className,
}: WorkflowViewFrameProps) {
  const [prefs, update] = useVizPrefs();
  const resolved = useMotion();
  const style = value ?? prefs.workflowView;
  const m = motion ?? resolved;
  const { Component, legend } = WORKFLOW_RENDERERS[style];
  return (
    <VizFrame
      title={title}
      description={
        description ?? `${snapshot.lines.length} workflows, ${snapshot.trains.length} runs`
      }
      styles={WORKFLOW_VIEWS}
      value={style}
      onChange={onChange ?? ((workflowView) => update({ workflowView }))}
      motion={m}
      legend={legend}
      className={className}
    >
      <div className="p-3">
        <Component
          snapshot={snapshot}
          motion={m}
          flowParticles={prefs.flowParticles}
          onSelectRun={onSelectRun}
          onSelectStation={onSelectStation}
        />
      </div>
    </VizFrame>
  );
}
