import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { PlaygroundHistoryScreen } from "./PlaygroundHistoryScreen";
import { HISTORY, LONG } from "./playground.fixtures";

const meta: Meta = {
  title: "Screens/Playground/PlaygroundHistoryScreen",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

export const Full: Story = {
  render: () => <PlaygroundHistoryScreen sessions={HISTORY} />,
};

/** No browser-local sessions have been saved. */
export const Empty: Story = {
  render: () => <PlaygroundHistoryScreen sessions={[]} />,
};

/** Browser storage failed; retry is explicit. */
export const Failed: Story = {
  render: () => <PlaygroundHistoryScreen sessions={[]} error />,
};

export const LongStrings: Story = {
  render: () => (
    <PlaygroundHistoryScreen
      sessions={[
        {
          ...HISTORY[0],
          title: LONG,
          messages: [{ role: "user", content: LONG }],
          modelName: LONG,
        },
      ]}
    />
  ),
};

export const Loading: Story = { render: () => <PlaygroundHistoryScreen sessions={[]} loading /> };
