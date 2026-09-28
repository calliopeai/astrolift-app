import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { CustomDomainsDoc } from "./CustomDomainsDoc";

const meta: Meta<typeof CustomDomainsDoc> = {
  title: "Screens/Documentation/CustomDomainsDoc",
  component: CustomDomainsDoc,
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj<typeof CustomDomainsDoc>;

/** Static copy: there is no loading, empty, error, or variable-length state. */
export const Full: Story = {};
