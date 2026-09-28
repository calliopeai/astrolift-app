import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { TeamDetailScreen } from "./TeamDetailScreen";
import { TeamMembersPanel } from "./TeamMembersPanel";
import {
  MEMBERS_PANEL,
  MEMBERS_PANEL_LONG,
  TEAM_DETAIL,
  TEAM_DETAIL_LONG,
} from "./teams-tokens.fixtures";

const meta: Meta = { title: "Screens/Teams/TeamDetailScreen" };
export default meta;

type Story = StoryObj;

function Screen({
  members = MEMBERS_PANEL,
  ...props
}: typeof TEAM_DETAIL & { members?: typeof MEMBERS_PANEL }) {
  return (
    <TeamDetailScreen
      {...props}
      renderMembers={(team, roles) => <TeamMembersPanel {...members} team={team} roles={roles} />}
    />
  );
}

export const Full: Story = { render: () => <Screen {...TEAM_DETAIL} /> };

export const Loading: Story = {
  render: () => <Screen {...TEAM_DETAIL} team={null} loading />,
};

/** A team with no members yet. */
export const Empty: Story = {
  render: () => <Screen {...TEAM_DETAIL} members={{ ...MEMBERS_PANEL, members: [] }} />,
};

/** The slug matched no team (or the team list failed): the screen's only error state. */
export const NotFound: Story = {
  render: () => <Screen {...TEAM_DETAIL} slug="no-such-team" team={null} />,
};

export const LongStrings: Story = {
  render: () => <Screen {...TEAM_DETAIL_LONG} members={MEMBERS_PANEL_LONG} />,
};
