import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";

import { LONG_TEAM, editTeamProps } from "../projects/projects-teams-dialogs.fixtures";

import { EditTeamSheet } from "./EditTeamSheet";

const meta: Meta = {
  title: "Screens/Teams/EditTeamSheet",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

/** Freshly opened: nothing changed, so the save stays disabled. */
export const Unchanged: Story = { render: () => <EditTeamSheet {...editTeamProps()} /> };

/** The availability check for a new slug is in flight. */
export const Checking: Story = {
  render: () => <EditTeamSheet {...editTeamProps({ slug: "core", slugStatus: "checking" })} />,
};

export const Available: Story = {
  render: () => (
    <EditTeamSheet {...editTeamProps({ slug: "core", slugStatus: "available", canSubmit: true })} />
  ),
};

export const Taken: Story = {
  render: () => <EditTeamSheet {...editTeamProps({ slug: "platform", slugStatus: "taken" })} />,
};

export const InvalidSlug: Story = {
  render: () => <EditTeamSheet {...editTeamProps({ slug: "-core", slugStatus: "invalid" })} />,
};

export const EmptySlug: Story = {
  render: () => <EditTeamSheet {...editTeamProps({ slug: "", slugStatus: "empty" })} />,
};

/** The update mutation is in flight. */
export const Saving: Story = {
  render: () => (
    <EditTeamSheet {...editTeamProps({ slug: "core", slugStatus: "available", saving: true })} />
  ),
};

export const LongStrings: Story = {
  render: () => (
    <EditTeamSheet
      {...editTeamProps({
        team: LONG_TEAM,
        name: LONG_TEAM.name,
        slug: LONG_TEAM.slug,
        slugStatus: "invalid",
      })}
    />
  ),
};
