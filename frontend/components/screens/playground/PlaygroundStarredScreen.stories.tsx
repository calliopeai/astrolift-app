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

/** No browser-local starred sessions. */
export const Empty: Story = {
  render: () => <PlaygroundStarredScreen items={[]} />,
};

export const LongStrings: Story = {
  render: () => (
    <PlaygroundStarredScreen
      items={[
        { ...STARRED[0], title: LONG, messages: [{ role: "user", content: `${LONG} ${LONG}` }] },
      ]}
    />
  ),
};

export const Loading: Story = { render: () => <PlaygroundStarredScreen items={[]} loading /> };
export const Failed: Story = { render: () => <PlaygroundStarredScreen items={[]} error /> };
