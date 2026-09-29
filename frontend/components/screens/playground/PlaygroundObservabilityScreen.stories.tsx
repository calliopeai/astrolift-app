import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { PlaygroundObservabilityScreen } from "./PlaygroundObservabilityScreen";
import { LONG, OBSERVABILITY } from "./playground.fixtures";

const meta: Meta = {
  title: "Screens/Playground/PlaygroundObservabilityScreen",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

/** The demo dataset: a failed run with retries and two config commits. */
export const Full: Story = {
  render: () => <PlaygroundObservabilityScreen {...OBSERVABILITY} />,
};

/** Static demo data, so no fetch; a run still in progress is the closest to loading. */
export const Loading: Story = {
  render: () => (
    <PlaygroundObservabilityScreen
      {...OBSERVABILITY}
      run={{ ...OBSERVABILITY.run, status: "running", endedAt: null, errorMessage: undefined }}
      activities={OBSERVABILITY.activities.map((a) =>
        a.status === "failed" ? { ...a, status: "running" } : a
      )}
    />
  ),
};

export const Empty: Story = {
  render: () => <PlaygroundObservabilityScreen {...OBSERVABILITY} activities={[]} commits={[]} />,
};

export const Failed: Story = {
  render: () => (
    <PlaygroundObservabilityScreen
      {...OBSERVABILITY}
      run={{
        ...OBSERVABILITY.run,
        errorStack: "Workflow execution failed\n  at ApplyManifest:attempt-2",
      }}
    />
  ),
};

export const LongStrings: Story = {
  render: () => (
    <PlaygroundObservabilityScreen
      {...OBSERVABILITY}
      run={{ ...OBSERVABILITY.run, workflowId: LONG, errorMessage: LONG }}
      commits={[{ ...OBSERVABILITY.commits[0], message: LONG, author: LONG }]}
    />
  ),
};
