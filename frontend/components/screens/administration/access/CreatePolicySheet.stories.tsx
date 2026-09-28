import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { CreatePolicySheet } from "./CreatePolicySheet";
import { CREATE_SHEET } from "./fixtures";

const meta: Meta = {
  title: "Screens/Administration/Access/CreatePolicySheet",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

/** Open with the default DENY / ORG / "*" draft and the time-window template. */
export const Open: Story = { render: () => <CreatePolicySheet {...CREATE_SHEET} /> };

export const Creating: Story = {
  render: () => <CreatePolicySheet {...CREATE_SHEET} creating />,
};
