import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";

import type { TopologyKind } from "@/lib/topology";
import type { AppView as AppStyle } from "@/lib/viz-prefs";

import { AppView } from "./AppView";
import { makeApp, stepApp, type AppSnapshot } from "./core/app-model";
import { mulberry32 } from "./core/semantics";
import { useSimulation } from "./core/use-simulation";

/** Advance a few steps so functions and agents carry recent events. */
function app(topology: TopologyKind, incident = false): AppSnapshot {
  let s = makeApp(topology, { incident });
  const rng = mulberry32(9);
  for (let i = 0; i < 3; i++) s = stepApp(s, rng, 1200);
  return s;
}

function Demo({
  initial,
  style,
  motion,
}: {
  initial: AppSnapshot;
  /** Omit to use the saved preference, as the page does. */
  style?: AppStyle;
  motion?: "full" | "reduced";
}) {
  const snapshot = useSimulation(initial, stepApp, { paused: motion === "reduced" });
  const [value, setValue] = React.useState(style);
  const [selected, setSelected] = React.useState<string>();
  return (
    <div className="space-y-2">
      <AppView
        snapshot={snapshot}
        value={value}
        onChange={style ? setValue : undefined}
        motion={motion}
        onSelectNode={setSelected}
      />
      <p className="text-muted-foreground text-xs">Selected: {selected ?? "none"}</p>
    </div>
  );
}

const meta: Meta<typeof Demo> = {
  title: "Viz/App/AppView",
  component: Demo,
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj<typeof Demo>;

// One per style, on the web + worker shape.
const styleStory = (style: AppStyle): Story => ({
  render: () => <Demo initial={app("service-worker")} style={style} />,
});

export const Auto = styleStory("auto");
export const Isometric = styleStory("isometric");
export const Graph = styleStory("graph");
export const Classic = styleStory("classic");

// One per topology, in the auto style, so each dashboard shape can be compared.
const topologyStory = (topology: TopologyKind): Story => ({
  render: () => <Demo initial={app(topology)} style="auto" />,
});

export const Service = topologyStory("service");
export const ServiceData = topologyStory("service-data");
export const ServiceWorker = topologyStory("service-worker");
export const Microservices = topologyStory("microservices");
export const ServiceAgent = topologyStory("service-agent");
export const Agent = topologyStory("agent");
export const Functions = topologyStory("functions");
export const Scheduled = topologyStory("scheduled");
export const Task = topologyStory("task");
export const Workflow = topologyStory("workflow");
export const Mixed = topologyStory("mixed");

/** Uncontrolled: the saved preference picks the style and the switcher saves it. */
export const Live: Story = { render: () => <Demo initial={app("microservices")} /> };

/** Reduced motion during an incident: still, with the same failing state. */
export const Reduced: Story = {
  render: () => <Demo initial={app("service-agent", true)} style="auto" motion="reduced" />,
};
