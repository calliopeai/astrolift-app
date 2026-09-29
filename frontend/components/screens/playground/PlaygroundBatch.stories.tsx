import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { PlaygroundBatch } from "./PlaygroundBatch";
import { BATCH, LONG } from "./playground.fixtures";

const meta: Meta = {
  title: "Screens/Playground/PlaygroundBatch",
};
export default meta;

type Story = StoryObj;

export const Full: Story = {
  render: () => <PlaygroundBatch {...BATCH} />,
};

/** Mid-run: the first result is back, the second is still going. */
export const Loading: Story = {
  render: () => <PlaygroundBatch {...BATCH} running results={BATCH.results.slice(0, 1)} />,
};

export const Empty: Story = {
  render: () => <PlaygroundBatch {...BATCH} input="" inputs={[]} results={[]} />,
};

/** A row that failed shows its error in place of the output. */
export const Failed: Story = {
  render: () => (
    <PlaygroundBatch
      {...BATCH}
      results={[
        BATCH.results[0],
        { input: BATCH.inputs[1], output: "", ok: false, error: "503 from inference gateway" },
      ]}
    />
  ),
};

export const LongStrings: Story = {
  render: () => (
    <PlaygroundBatch
      {...BATCH}
      input={LONG}
      inputs={[LONG]}
      results={[{ input: LONG, output: LONG, ok: true }]}
    />
  ),
};
