import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { EMPTY_INDEX_SECTIONS, LONG_INDEX_SECTIONS } from "./docs-a.fixtures";
import { DocumentationIndexScreen } from "./DocumentationIndexScreen";

const meta: Meta<typeof DocumentationIndexScreen> = {
  title: "Screens/Documentation/DocumentationIndexScreen",
  component: DocumentationIndexScreen,
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj<typeof DocumentationIndexScreen>;

export const Full: Story = {};

/** Static content: no sections is the closest thing to an empty state. */
export const Empty: Story = { args: { sections: EMPTY_INDEX_SECTIONS } };

export const LongStrings: Story = { args: { sections: LONG_INDEX_SECTIONS } };
