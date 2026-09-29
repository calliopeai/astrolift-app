"use client";

import * as React from "react";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { appRendering } from "@/components/viz/AppView";
import { makeApp, stepApp } from "@/components/viz/core/app-model";
import { makeFleet, stepFleet, type FleetSnapshot } from "@/components/viz/core/fleet-model";
import { useSimulation } from "@/components/viz/core/use-simulation";
import { makeWorkflows, stepWorkflows } from "@/components/viz/core/workflow-model";
import { stepRuns } from "@/components/viz/fleet/manifest";
import { FLEET_RENDERERS } from "@/components/viz/FleetView";
import { WORKFLOW_RENDERERS } from "@/components/viz/WorkflowView";
import { cn } from "@/lib/utils";
import {
  APP_VIEWS,
  FLEET_VIEWS,
  MOTION_MODES,
  WORKFLOW_VIEWS,
  type AppView,
  type FleetView,
  type Motion,
  type WorkflowView,
} from "@/lib/viz-prefs";

import type { useVisualizations } from "./use-visualizations";

type Option = { label: string; blurb: string };

/** A row of choices, each with its blurb; the chosen one is pressed. */
function Options<K extends string>({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: Record<K, Option>;
  value: K;
  onChange: (key: K) => void;
}) {
  return (
    <div role="group" aria-label={label} className="grid grid-cols-2 gap-2 sm:grid-cols-4">
      {(Object.keys(options) as K[]).map((key) => (
        <button
          key={key}
          type="button"
          aria-pressed={value === key}
          onClick={() => onChange(key)}
          className={cn(
            "border-border hover:border-primary rounded-md border px-3 py-2 text-left transition-colors",
            value === key && "border-primary ring-primary ring-1"
          )}
        >
          <span className="block text-sm font-semibold">{options[key].label}</span>
          <span className="text-muted-foreground block text-xs">{options[key].blurb}</span>
        </button>
      ))}
    </div>
  );
}

/**
 * A small live picture of the chosen style. Inert: it is there to look at,
 * so it takes no focus and no clicks. The simulation pauses under reduced
 * motion, matching what the real views do.
 */
function Preview({ children }: { children: React.ReactNode }) {
  return (
    <div className="mt-3">
      <p className="text-muted-foreground text-2xs mb-1.5 font-semibold tracking-[0.08em] uppercase">
        Preview
      </p>
      <div inert className="bg-background max-h-72 overflow-hidden rounded-md border p-3">
        {children}
      </div>
    </div>
  );
}

function stepFleetAndRuns(s: FleetSnapshot, rng: () => number, dt: number): FleetSnapshot {
  return stepRuns(stepFleet(s, rng, dt), rng);
}

function FleetPreview({ style, motion }: { style: FleetView; motion: Motion }) {
  const [initial] = React.useState(() => makeFleet({ clusters: 2, agentsPerCluster: 5 }));
  const snapshot = useSimulation(initial, stepFleetAndRuns, { paused: motion === "reduced" });
  const { Component } = FLEET_RENDERERS[style];
  return <Component snapshot={snapshot} motion={motion} />;
}

function WorkflowPreview({
  style,
  motion,
  flowParticles,
}: {
  style: WorkflowView;
  motion: Motion;
  flowParticles: boolean;
}) {
  const [initial] = React.useState(() => makeWorkflows({ lines: 2, trainsPerLine: 2 }));
  const snapshot = useSimulation(initial, stepWorkflows, { paused: motion === "reduced" });
  const { Component } = WORKFLOW_RENDERERS[style];
  return <Component snapshot={snapshot} motion={motion} flowParticles={flowParticles} />;
}

function AppPreview({
  style,
  motion,
  flowParticles,
}: {
  style: AppView;
  motion: Motion;
  flowParticles: boolean;
}) {
  const [initial] = React.useState(() => makeApp("service-worker", { name: "storefront" }));
  const snapshot = useSimulation(initial, stepApp, { paused: motion === "reduced" });
  return <>{appRendering(style, { snapshot, motion, flowParticles }).node}</>;
}

export type VisualizationsScreenProps = ReturnType<typeof useVisualizations>;

/**
 * Settings › Visualizations. Pure: the preferences, their setter and the
 * motion they resolve to on this device come from useVisualizations.
 */
export function VisualizationsScreen({ value, onChange, motion }: VisualizationsScreenProps) {
  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardHeader>
          <CardTitle>Fleet view</CardTitle>
          <CardDescription>
            How agent fleets are drawn. Every style shows the same agents and the same health.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Options
            label="Fleet view"
            options={FLEET_VIEWS}
            value={value.fleetView}
            onChange={(fleetView) => onChange({ fleetView })}
          />
          <Preview>
            <FleetPreview style={value.fleetView} motion={motion} />
          </Preview>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Workflow view</CardTitle>
          <CardDescription>How workflows and their runs are drawn.</CardDescription>
        </CardHeader>
        <CardContent>
          <Options
            label="Workflow view"
            options={WORKFLOW_VIEWS}
            value={value.workflowView}
            onChange={(workflowView) => onChange({ workflowView })}
          />
          <Preview>
            <WorkflowPreview
              style={value.workflowView}
              motion={motion}
              flowParticles={value.flowParticles}
            />
          </Preview>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>App view</CardTitle>
          <CardDescription>
            How an app&apos;s dashboard is drawn. Auto lays it out for the app&apos;s shape.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Options
            label="App view"
            options={APP_VIEWS}
            value={value.appView}
            onChange={(appView) => onChange({ appView })}
          />
          <Preview>
            <AppPreview style={value.appView} motion={motion} flowParticles={value.flowParticles} />
          </Preview>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Motion</CardTitle>
          <CardDescription>
            Every animation stands for real state. Reduced motion draws still pictures that carry
            the same information.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <Options
            label="Motion"
            options={MOTION_MODES}
            value={value.motion}
            onChange={(m) => onChange({ motion: m })}
          />
          <div>
            <p className="text-muted-foreground text-2xs mb-2 font-semibold tracking-[0.08em] uppercase">
              Flow particles
            </p>
            <Options
              label="Flow particles"
              options={{
                on: { label: "On", blurb: "Particles along edges show throughput" },
                off: { label: "Off", blurb: "Edges only, with counts" },
              }}
              value={value.flowParticles ? "on" : "off"}
              onChange={(v) => onChange({ flowParticles: v === "on" })}
            />
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
