import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { PlaygroundStarredScreen } from "./PlaygroundStarredScreen";
import { LONG, STARRED } from "./playground.fixtures";

const meta: Meta = {
  title: "Screens/Playground/PlaygroundStarredScreen",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

export const Full: Story = {
  render: () => <PlaygroundStarredScreen items={STARRED} />,
};

/** Static list: no loading or error state exists; an empty list is the closest to both. */
export const Empty: Story = {
  render: () => <PlaygroundStarredScreen items={[]} />,
};

export const LongStrings: Story = {
  render: () => (
    <PlaygroundStarredScreen items={[{ ...STARRED[0], title: LONG, prompt: `${LONG} ${LONG}` }]} />
  ),
};
