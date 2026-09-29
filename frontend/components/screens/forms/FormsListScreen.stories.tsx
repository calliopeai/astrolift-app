import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { FormsListScreen } from "./FormsListScreen";
import { LIST, LONG_FORM } from "./forms.fixtures";

const meta: Meta = {
  title: "Screens/Forms/FormsListScreen",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

export const Full: Story = { render: () => <FormsListScreen {...LIST} /> };

export const Loading: Story = {
  render: () => <FormsListScreen {...LIST} forms={[]} loading />,
};

export const Empty: Story = { render: () => <FormsListScreen {...LIST} forms={[]} /> };

export const LoadFailed: Story = {
  render: () => (
    <FormsListScreen {...LIST} forms={[]} error={{ message: "Network error: 502 Bad Gateway" }} />
  ),
};

export const LongStrings: Story = {
  render: () => <FormsListScreen {...LIST} forms={[LONG_FORM, ...LIST.forms]} />,
};
