import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { ASSIGN_PROJECT, LONG } from "./app-overview-cards-a.fixtures";
import { AssignProjectCardView } from "./AssignProjectCard";

const meta: Meta<typeof AssignProjectCardView> = {
  title: "Screens/Apps/Overview/AssignProjectCard",
  component: AssignProjectCardView,
  args: ASSIGN_PROJECT,
};
export default meta;

type Story = StoryObj<typeof AssignProjectCardView>;

/** Assigned to a project; the picker offers every assignable project by team. */
export const Full: Story = {};

/** The assignable-projects query is in flight with nothing cached. */
export const Loading: Story = { args: { projectsLoading: true } };

/** No projects the viewer may assign to: the picker is disabled. */
export const Empty: Story = {
  args: { currentProjectId: null, currentProjectName: "", currentTeamName: "", byTeam: [] },
};

/**
 * The card has no error state of its own: a failed assign toasts and the
 * card stays as it was. Closest real state: unassigned, with projects to pick.
 */
export const Unassigned: Story = {
  args: { currentProjectId: null, currentProjectName: "", currentTeamName: "" },
};

/** The assign / unassign mutation in flight. */
export const Saving: Story = { args: { assigning: true } };

/** Viewer without app.update: current value only, no picker. */
export const ReadOnly: Story = { globals: { permissions: "none" } };

export const LongStrings: Story = {
  args: {
    currentProjectName: LONG,
    currentTeamName: LONG,
    byTeam: [
      {
        teamName: LONG,
        teamSlug: LONG,
        projects: [
          { id: "proj-1", slug: LONG, name: LONG, team: { id: "t-9", slug: LONG, name: LONG } },
        ],
      },
    ],
  },
};
