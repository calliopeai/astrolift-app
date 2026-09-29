import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { PoliciesDocScreen } from "./PoliciesDocScreen";

/**
 * A static documentation page: it takes no props and has no loading, empty,
 * error, or long-strings state, so Full is the only real state.
 */
const meta: Meta<typeof PoliciesDocScreen> = {
  title: "Screens/Documentation/PoliciesDocScreen",
  component: PoliciesDocScreen,
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj<typeof PoliciesDocScreen>;

export const Full: Story = {};
