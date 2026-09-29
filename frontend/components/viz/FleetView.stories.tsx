import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";

import type { FleetView as FleetStyle } from "@/lib/viz-prefs";

import { makeFleet, stepFleet, type FleetSnapshot } from "./core/fleet-model";
import { mulberry32 } from "./core/semantics";
import { useSimulation } from "./core/use-simulation";
import { makeRuns, stepRuns } from "./fleet/manifest";
import { FleetView } from "./FleetView";

/** The fleet model plus run movement, so the manifest board moves too. */
function step(s: FleetSnapshot, rng: () => number, dt: number): FleetSnapshot {
  return stepRuns(stepFleet(s, rng, dt), rng);
}

function fleet(opts: Parameters<typeof makeFleet>[0] = {}, warmSteps = 3): FleetSnapshot {
  const base = makeFleet({ clusters: 3, agentsPerCluster: 6, ...opts });
  let s: FleetSnapshot = { ...base, runs: makeRuns(base, { count: 12 }) };
  const rng = mulberry32(5);
  for (let i = 0; i < warmSteps; i++) s = step(s, rng, 1200);
  return s;
}

function Demo({
  initial,
  style,
  motion,
  live = true,
}: {
  initial: FleetSnapshot;
  /** Omit to use the saved preference, as the page does. */
  style?: FleetStyle;
  motion?: "full" | "reduced";
  live?: boolean;
}) {
  const snapshot = useSimulation(initial, step, { paused: !live || motion === "reduced" });
  const [value, setValue] = React.useState(style);
  const [selected, setSelected] = React.useState<string>();
  return (
    <FleetView
      snapshot={snapshot}
      value={value}
      onChange={style ? setValue : undefined}
      motion={motion}
      selectedAgentId={selected}
      onSelectAgent={setSelected}
    />
  );
}

const meta: Meta<typeof Demo> = {
  title: "Viz/Fleet/FleetView",
  component: Demo,
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj<typeof Demo>;

const styleStory = (style: FleetStyle): Story => ({
  render: () => <Demo initial={fleet()} style={style} />,
});

export const Orbit = styleStory("orbit");
export const Heartbeat = styleStory("heartbeat");
export const Hive = styleStory("hive");
export const Manifest = styleStory("manifest");
export const Isometric = styleStory("isometric");
export const Graph = styleStory("graph");
export const List = styleStory("list");

/** Uncontrolled: the saved preference picks the style and the switcher saves it. */
export const Live: Story = {
  render: () => <Demo initial={fleet({ clusters: 4, agentsPerCluster: 10, failing: 0.05 })} />,
};

/** Reduced motion: every style draws a still frame carrying the same state. */
export const Reduced: Story = {
  render: () => <Demo initial={fleet({ failing: 0.1 })} style="orbit" motion="reduced" />,
};
