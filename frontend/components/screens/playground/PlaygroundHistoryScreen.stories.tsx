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

/** The page reads a static list, so there is no loading state; an empty list is the closest. */
export const Empty: Story = {
  render: () => <PlaygroundHistoryScreen sessions={[]} />,
};

/** No page-level error exists; a session that ended in error is the closest. */
export const Failed: Story = {
  render: () => <PlaygroundHistoryScreen sessions={HISTORY.filter((s) => s.status === "error")} />,
};

export const LongStrings: Story = {
  render: () => (
    <PlaygroundHistoryScreen
      sessions={[{ ...HISTORY[0], prompt: LONG, model: "Genesis-long-context-preview-2026-09" }]}
    />
  ),
};
