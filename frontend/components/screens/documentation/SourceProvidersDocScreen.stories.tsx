import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { SourceProvidersDocScreen } from "./SourceProvidersDocScreen";

/**
 * A static documentation page: it takes no props and has no loading, empty,
 * error, or long-strings state, so Full is the only real state.
 */
const meta: Meta<typeof SourceProvidersDocScreen> = {
  title: "Screens/Documentation/SourceProvidersDocScreen",
  component: SourceProvidersDocScreen,
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj<typeof SourceProvidersDocScreen>;

export const Full: Story = {};
