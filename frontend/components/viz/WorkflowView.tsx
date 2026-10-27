"use client";

import { useTranslations } from "next-intl";
import * as React from "react";
import { ExpandIcon, MinusIcon, PlusIcon, ScanIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogTitle, DialogDescription } from "@/components/ui/dialog";

import {
  WORKFLOW_VIEWS,
  useMotion,
  useVizPrefs,
  type WorkflowView as WorkflowStyle,
} from "@/lib/viz-prefs";

import { VizFrame, useStyleCopy } from "./core/VizFrame";
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
  title,
  description,
  value,
  onChange,
  motion,
  className,
}: WorkflowViewFrameProps) {
  const t = useTranslations("vizRender");
  const styles = useStyleCopy(WORKFLOW_VIEWS, "workflow");
  const [prefs, update] = useVizPrefs();
  const resolved = useMotion();
  const style = value ?? prefs.workflowView;
  const m = motion ?? resolved;
  const { Component, legend } = WORKFLOW_RENDERERS[style];
  const [expanded, setExpanded] = React.useState(false);
  const [zoom, setZoom] = React.useState(1);
  const expandButton = React.useRef<HTMLButtonElement>(null);
  const viewport = React.useRef<HTMLDivElement>(null);
  const width = Math.max(900, ...snapshot.lines.map((line) => line.stations.length * 180));
  const canvas = (full: boolean) => (
    <VizFrame
      title={title ?? t("frame.workflowsTitle")}
      description={
        description ??
        t("frame.workflowsDescription", {
          workflows: snapshot.lines.length,
          runs: snapshot.trains.length,
        })
      }
      styles={styles}
      value={style}
      onChange={onChange ?? ((workflowView) => update({ workflowView }))}
      motion={m}
      legend={legend}
      className={full ? "flex h-full min-h-0 flex-col border-0" : className}
      bodyClassName={full ? "flex-1" : undefined}
      collapsibleLegend
      actions={
        <>
          <Button
            size="icon"
            variant="ghost"
            aria-label={t("canvas.zoomOut")}
            onClick={() => setZoom((z) => Math.max(0.25, z - 0.25))}
          >
            <MinusIcon className="size-4" />
          </Button>
          <span className="text-muted-foreground text-xs tabular-nums">
            {Math.round(zoom * 100)}%
          </span>
          <Button
            size="icon"
            variant="ghost"
            aria-label={t("canvas.zoomIn")}
            onClick={() => setZoom((z) => Math.min(3, z + 0.25))}
          >
            <PlusIcon className="size-4" />
          </Button>
          <Button
            size="icon"
            variant="ghost"
            aria-label={t("canvas.fit")}
            onClick={() => setZoom(Math.min(1, (viewport.current?.clientWidth ?? width) / width))}
          >
            <ScanIcon className="size-4" />
          </Button>
          {!full && (
            <Button
              ref={expandButton}
              size="sm"
              variant="outline"
              onClick={() => setExpanded(true)}
            >
              <ExpandIcon className="size-4" />
              {t("canvas.expand")}
            </Button>
          )}
        </>
      }
    >
      <div
        ref={viewport}
        tabIndex={0}
        aria-label={t("canvas.pan")}
        className={full ? "h-full overflow-auto p-4" : "h-96 overflow-auto p-4"}
      >
        <div
          className="flex min-h-full items-center"
          style={{ width: style === "list" ? "100%" : width * zoom }}
        >
          <Component
            snapshot={snapshot}
            motion={m}
            flowParticles={prefs.flowParticles}
            onSelectRun={onSelectRun}
            onSelectStation={onSelectStation}
          />
        </div>
      </div>
    </VizFrame>
  );
  return (
    <>
      {!expanded && canvas(false)}
      <Dialog open={expanded} onOpenChange={setExpanded}>
        <DialogContent
          onCloseAutoFocus={(event) => {
            event.preventDefault();
            requestAnimationFrame(() => expandButton.current?.focus());
          }}
          className="flex h-[calc(100dvh-2rem)] w-[calc(100vw-2rem)] max-w-none flex-col gap-0 p-2 sm:max-w-none"
        >
          <DialogTitle className="sr-only">{title ?? t("frame.workflowsTitle")}</DialogTitle>
          <DialogDescription className="sr-only">{t("canvas.pan")}</DialogDescription>
          {expanded && canvas(true)}
        </DialogContent>
      </Dialog>
    </>
  );
}
