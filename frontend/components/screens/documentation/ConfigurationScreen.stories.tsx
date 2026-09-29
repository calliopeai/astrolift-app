import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { CONFIGURATION_GROUPS } from "./configuration-groups";
import { ConfigurationScreen } from "./ConfigurationScreen";
import {
  DOCS_B_GROUPS_EMPTY,
  DOCS_B_GROUPS_FULL,
  DOCS_B_GROUPS_LONG,
  DOCS_B_GROUPS_NO_VARS,
} from "./docs-b.fixtures";

const meta: Meta<typeof ConfigurationScreen> = {
  title: "Screens/Documentation/ConfigurationScreen",
  component: ConfigurationScreen,
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj<typeof ConfigurationScreen>;

/** Static content: there is no loading or error state. */
export const Full: Story = { args: { groups: DOCS_B_GROUPS_FULL } };

/** The real reference content the route renders. */
export const Production: Story = { args: { groups: CONFIGURATION_GROUPS } };

export const Empty: Story = { args: { groups: DOCS_B_GROUPS_EMPTY } };

export const GroupWithoutVariables: Story = { args: { groups: DOCS_B_GROUPS_NO_VARS } };

export const LongStrings: Story = { args: { groups: DOCS_B_GROUPS_LONG } };
