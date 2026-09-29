import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { ClusterPrerequisitesScreen } from "./ClusterPrerequisitesScreen";

const meta: Meta<typeof ClusterPrerequisitesScreen> = {
  title: "Screens/Documentation/ClusterPrerequisitesScreen",
  component: ClusterPrerequisitesScreen,
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj<typeof ClusterPrerequisitesScreen>;

/** Static copy: there is no loading, empty, error, or variable-length state. */
export const Full: Story = {};
