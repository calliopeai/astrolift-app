import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { ChangelogScreen } from "./ChangelogScreen";
import { EMPTY_CHANGELOG, LONG_CHANGELOG } from "./docs-a.fixtures";

const meta: Meta<typeof ChangelogScreen> = {
  title: "Screens/Documentation/ChangelogScreen",
  component: ChangelogScreen,
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj<typeof ChangelogScreen>;

/** Without a verified versioned feed the page links the canonical repository history. */
export const Full: Story = {};

/** Static content: no entries is the closest thing to an empty state. */
export const Empty: Story = { args: { entries: EMPTY_CHANGELOG } };

export const LongStrings: Story = { args: { entries: LONG_CHANGELOG } };
