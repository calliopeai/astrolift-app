import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { userEvent, within } from "storybook/test";

import { TeamMembersPanel } from "./TeamMembersPanel";
import { MEMBERS_PANEL, MEMBERS_PANEL_LONG } from "./teams-tokens.fixtures";

const meta: Meta = { title: "Screens/Teams/TeamMembersPanel" };
export default meta;

type Story = StoryObj;

export const Full: Story = { render: () => <TeamMembersPanel {...MEMBERS_PANEL} /> };

/** A viewer without team.manage_members: no checkboxes, no bulk footer. */
export const ReadOnly: Story = {
  render: () => <TeamMembersPanel {...MEMBERS_PANEL} canManageTeamMembers={false} />,
};

/** Two rows checked: the sticky bulk-assign footer shows. */
export const Selected: Story = {
  render: () => <TeamMembersPanel {...MEMBERS_PANEL} />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(canvas.getByRole("checkbox", { name: /leo/ }));
    await userEvent.click(canvas.getByRole("checkbox", { name: /keith/ }));
  },
};

export const Loading: Story = {
  render: () => <TeamMembersPanel {...MEMBERS_PANEL} members={[]} loading />,
};

export const Empty: Story = {
  render: () => <TeamMembersPanel {...MEMBERS_PANEL} members={[]} />,
};

export const LoadFailed: Story = {
  render: () => (
    <TeamMembersPanel {...MEMBERS_PANEL} error={new globalThis.Error("upstream timed out")} />
  ),
};

export const LongStrings: Story = { render: () => <TeamMembersPanel {...MEMBERS_PANEL_LONG} /> };
