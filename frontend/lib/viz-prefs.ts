"use client";

import * as React from "react";

/**
 * Visualization preferences: how a person likes fleets, workflows and app
 * dashboards drawn. Every style renders the same data model and the same
 * status semantics; only the picture changes (spec 44 viz addendum).
 *
 * Stored client-side like `lib/appearance.ts`: a per-person display choice.
 * There is no server-side UI preference store yet; `STORAGE_KEY` is the only
 * seam to move when one exists. Reads are defensive, as in appearance.
 */

export const FLEET_VIEWS = {
  orbit: { label: "Orbit", blurb: "Clusters as planets, agents as satellites" },
  heartbeat: { label: "Heartbeat", blurb: "A fading phosphor dot per event" },
  hive: { label: "Hive", blurb: "A tile per agent, for large fleets" },
  manifest: { label: "Manifest", blurb: "The run queue as a departures board" },
  isometric: { label: "Isometric", blurb: "Clusters as platforms, load as height" },
  graph: { label: "Graph", blurb: "A live force graph with dispatch pulses" },
  swarm: { label: "Swarm", blurb: "Agents swarming their clusters on tendrils" },
  list: { label: "List", blurb: "A plain table" },
} as const;

export const WORKFLOW_VIEWS = {
  transit: { label: "Transit", blurb: "Workflows as lines, runs as trains" },
  isometric: { label: "Isometric", blurb: "Stages as an assembly line" },
  graph: { label: "Graph", blurb: "An animated stage graph" },
  list: { label: "List", blurb: "A plain table" },
} as const;

export const APP_VIEWS = {
  auto: { label: "Auto", blurb: "Laid out for the app's topology" },
  isometric: { label: "Isometric", blurb: "Workloads as blocks on a platform" },
  graph: { label: "Graph", blurb: "Workloads and traffic as a live graph" },
  classic: { label: "Classic", blurb: "Cards only, no diagram" },
} as const;

export const MOTION_MODES = {
  system: { label: "Follow system", blurb: "Reduce motion when the OS asks" },
  full: { label: "Full", blurb: "Every animation" },
  reduced: { label: "Reduced", blurb: "Static pictures, same information" },
} as const;

export type FleetView = keyof typeof FLEET_VIEWS;
export type WorkflowView = keyof typeof WORKFLOW_VIEWS;
export type AppView = keyof typeof APP_VIEWS;
export type MotionMode = keyof typeof MOTION_MODES;
/** What a renderer actually does: animate, or draw the same state still. */
export type Motion = "full" | "reduced";

export interface VizPrefs {
  fleetView: FleetView;
  workflowView: WorkflowView;
  appView: AppView;
  /** Particles along workflow and app edges showing throughput. */
  flowParticles: boolean;
  motion: MotionMode;
}

export const DEFAULT_VIZ_PREFS: VizPrefs = {
  fleetView: "orbit",
  workflowView: "transit",
  appView: "auto",
  flowParticles: true,
  motion: "system",
};

export const STORAGE_KEY = "astrolift.viz";

function pick<T extends string>(value: unknown, allowed: Record<T, unknown>, fallback: T): T {
  return typeof value === "string" && value in allowed ? (value as T) : fallback;
}

/** Parse whatever is stored, dropping unknown or stale values field by field. */
export function parseVizPrefs(raw: string | null): VizPrefs {
  if (!raw) return DEFAULT_VIZ_PREFS;
  let data: Record<string, unknown>;
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return DEFAULT_VIZ_PREFS;
    data = parsed as Record<string, unknown>;
  } catch {
    return DEFAULT_VIZ_PREFS;
  }
  const d = DEFAULT_VIZ_PREFS;
  return {
    fleetView: pick(data.fleetView, FLEET_VIEWS, d.fleetView),
    workflowView: pick(data.workflowView, WORKFLOW_VIEWS, d.workflowView),
    appView: pick(data.appView, APP_VIEWS, d.appView),
    flowParticles: typeof data.flowParticles === "boolean" ? data.flowParticles : d.flowParticles,
    motion: pick(data.motion, MOTION_MODES, d.motion),
  };
}

// One store for the page, so every view and the settings screen move together.
const listeners = new Set<() => void>();
let cached: VizPrefs | null = null;

function read(): VizPrefs {
  if (cached) return cached;
  try {
    cached = parseVizPrefs(window.localStorage.getItem(STORAGE_KEY));
  } catch {
    cached = DEFAULT_VIZ_PREFS;
  }
  return cached;
}

function write(next: VizPrefs) {
  cached = next;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  } catch {
    // Private mode or blocked storage: the choice lasts for this page only.
  }
  listeners.forEach((l) => l());
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  const onStorage = (e: StorageEvent) => {
    if (e.key !== STORAGE_KEY) return;
    cached = null;
    listener();
  };
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", onStorage);
  };
}

export function useVizPrefs(): [VizPrefs, (patch: Partial<VizPrefs>) => void] {
  const prefs = React.useSyncExternalStore(subscribe, read, () => DEFAULT_VIZ_PREFS);
  const update = React.useCallback(
    (patch: Partial<VizPrefs>) => write({ ...read(), ...patch }),
    []
  );
  return [prefs, update];
}

const REDUCED_QUERY = "(prefers-reduced-motion: reduce)";

function subscribeReduced(listener: () => void) {
  if (typeof window.matchMedia !== "function") return () => {};
  const mq = window.matchMedia(REDUCED_QUERY);
  mq.addEventListener?.("change", listener);
  return () => mq.removeEventListener?.("change", listener);
}

function systemReduced() {
  return typeof window.matchMedia === "function" && window.matchMedia(REDUCED_QUERY).matches;
}

/** Resolve the motion a renderer should use from the preference and the OS. */
export function resolveMotion(mode: MotionMode, osReduced: boolean): Motion {
  if (mode === "system") return osReduced ? "reduced" : "full";
  return mode;
}

export function useMotion(): Motion {
  const [{ motion }] = useVizPrefs();
  const osReduced = React.useSyncExternalStore(subscribeReduced, systemReduced, () => false);
  return resolveMotion(motion, osReduced);
}
