import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";

import type { WorkflowView as WorkflowStyle } from "@/lib/viz-prefs";

import { useSimulation } from "./core/use-simulation";
import { makeWorkflows, stepWorkflows, type WorkflowSnapshot } from "./core/workflow-model";
import { WorkflowView } from "./WorkflowView";

function Demo({
  initial,
  style,
  motion,
}: {
  initial: WorkflowSnapshot;
  /** Omit to use the saved preference, as the page does. */
  style?: WorkflowStyle;
  motion?: "full" | "reduced";
}) {
  const snapshot = useSimulation(initial, stepWorkflows, { paused: motion === "reduced" });
  const [value, setValue] = React.useState(style);
  const [selected, setSelected] = React.useState<string>();
  return (
    <div className="space-y-2">
      <WorkflowView
        snapshot={snapshot}
        value={value}
        onChange={style ? setValue : undefined}
        motion={motion}
        onSelectRun={setSelected}
        onSelectStation={(_line, station) => setSelected(station)}
      />
      <p className="text-muted-foreground text-xs">Selected: {selected ?? "none"}</p>
    </div>
  );
}

const meta: Meta<typeof Demo> = {
  title: "Viz/Workflow/WorkflowView",
  component: Demo,
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj<typeof Demo>;

const styleStory = (style: WorkflowStyle): Story => ({
  render: () => <Demo initial={makeWorkflows()} style={style} />,
});

export const Transit = styleStory("transit");
export const Isometric = styleStory("isometric");
export const Graph = styleStory("graph");
export const List = styleStory("list");

/** Uncontrolled: the saved preference picks the style and the switcher saves it. */
export const Live: Story = {
  render: () => <Demo initial={makeWorkflows({ lines: 4, trainsPerLine: 3 })} />,
};

/** Reduced motion: a still picture with the same holds, failures and backlogs. */
export const Reduced: Story = {
  render: () => (
    <Demo initial={makeWorkflows({ backedUp: true, seed: 5 })} style="transit" motion="reduced" />
  ),
};
