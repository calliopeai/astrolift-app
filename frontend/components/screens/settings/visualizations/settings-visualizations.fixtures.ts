/**
 * Hand-typed fixtures for Settings › Visualizations, typed against the
 * screen's props.
 */
import { DEFAULT_VIZ_PREFS } from "@/lib/viz-prefs";

import type { useVisualizations } from "./use-visualizations";

type Fixture = ReturnType<typeof useVisualizations>;

const noop = () => {};

/** The defaults: orbit, transit, auto, particles on, follow the system (not reduced). */
export const VISUALIZATIONS: Fixture = {
  value: DEFAULT_VIZ_PREFS,
  onChange: noop,
  motion: "full",
};

/** Plain choices: list, list, classic, no particles, full motion. */
export const PLAIN: Fixture = {
  value: {
    fleetView: "list",
    workflowView: "list",
    appView: "classic",
    flowParticles: false,
    motion: "full",
  },
  onChange: noop,
  motion: "full",
};

/** Reduced motion chosen: previews are still frames. */
export const REDUCED: Fixture = {
  value: { ...DEFAULT_VIZ_PREFS, fleetView: "hive", workflowView: "graph", motion: "reduced" },
  onChange: noop,
  motion: "reduced",
};
