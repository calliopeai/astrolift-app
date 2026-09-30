import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";

import { CreateProjectSheet } from "./CreateProjectSheet";
import { LONG_TEAM, TEAMS, createProjectProps } from "./projects-teams-dialogs.fixtures";

const meta: Meta = {
  title: "Screens/Projects/CreateProjectSheet",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

export const Open: Story = { render: () => <CreateProjectSheet {...createProjectProps()} /> };

export const RequestedTeam: Story = {
  render: () => <CreateProjectSheet {...createProjectProps()} initialTeamSlug={TEAMS[1].slug} />,
};

/** The create mutation is in flight. */
export const Creating: Story = {
  render: () => <CreateProjectSheet {...createProjectProps({ creating: true })} />,
};

/** No teams yet: the picker is empty and the submit stays disabled. */
export const NoTeams: Story = {
  render: () => <CreateProjectSheet {...createProjectProps({ teams: [] })} />,
};

export const LongStrings: Story = {
  render: () => <CreateProjectSheet {...createProjectProps({ teams: [LONG_TEAM, ...TEAMS] })} />,
};
