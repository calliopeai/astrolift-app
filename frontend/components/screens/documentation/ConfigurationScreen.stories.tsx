import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";

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

/** A group's title filters the one list to that group's variables. */
export const OneGroup: Story = {
  args: { groups: DOCS_B_GROUPS_FULL },
  play: async ({ canvasElement }) => {
    const c = within(canvasElement);
    await userEvent.click(c.getByRole("button", { name: "Cache & queue" }));
    await expect(c.getByText("REDIS_URL")).toBeInTheDocument();
    await expect(c.queryByText("DJANGO_SECRET_KEY")).toBeNull();
  },
};

/** The image badge is backed by an actual shipped Dockerfile default. */
export const ImageDefaults: Story = {
  args: { groups: CONFIGURATION_GROUPS },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(canvas.getByRole("button", { name: "Control-plane image" }));
    await expect(canvas.getByText("PYTHONUNBUFFERED")).toBeInTheDocument();
    await expect(canvas.queryByText("DJANGO_SECRET_KEY")).toBeNull();
  },
};
