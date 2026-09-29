import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";

import { ProjectDetailScreen } from "./ProjectDetailScreen";
import {
  AGENTS,
  APPS,
  DETAIL,
  DETAIL_EMPTY,
  LIVE_AGENTS,
  LONG,
  MEMBERS,
  PROJECT,
  WORKFLOWS,
} from "./projects-detail.fixtures";

const meta: Meta<typeof ProjectDetailScreen> = {
  title: "Screens/Projects/ProjectDetailScreen",
  component: ProjectDetailScreen,
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj<typeof ProjectDetailScreen>;

/** Apps, agents, a workflow with a run in flight, and direct members. */
export const Full: Story = { args: DETAIL };

export const AgentOnly: Story = {
  args: { ...DETAIL, apps: [], workflows: [], workflowRuns: [] },
};

export const AgentWorkflowProject: Story = { args: { ...DETAIL, apps: [] } };

export const AppsOnly: Story = {
  args: { ...DETAIL, agents: [], liveAgents: [], workflows: [], workflowRuns: [] },
};

export const Loading: Story = { args: { ...DETAIL, project: null, loading: true } };

/** The project resolved; the workload queries are still in flight. */
export const WorkloadsLoading: Story = {
  args: { ...DETAIL_EMPTY, appsLoading: true, appsLoaded: false, membersLoading: true },
};

export const NotFound: Story = { args: { ...DETAIL, project: null, slug: "no-such-project" } };

/** Nothing registered yet. */
export const Empty: Story = { args: DETAIL_EMPTY };

/** Agent module hidden: the neutral "No visible workloads" empty state. */
export const NoVisibleWorkloads: Story = {
  args: { ...DETAIL_EMPTY, canViewAgents: false, canCreateAgent: false },
};

/** Apps failed to load, and so did the workflow topology. */
export const WorkloadError: Story = {
  args: { ...DETAIL, apps: [], appsError: true, workflows: [], workflowsError: true },
};

export const LongStrings: Story = {
  args: {
    ...DETAIL,
    slug: LONG,
    project: {
      ...PROJECT,
      slug: LONG,
      name: `Project ${LONG}`,
      team: { ...PROJECT.team, slug: `team-${LONG}` },
      organization: { ...PROJECT.organization, slug: `org-${LONG}` },
    },
    agents: AGENTS.map((row) => ({
      ...row,
      name: `${row.name} ${LONG}`,
      sourceRepo: `steadymd/${LONG}`,
    })),
    liveAgents: LIVE_AGENTS,
    workflows: WORKFLOWS.map((row) => ({ ...row, name: `${row.name} ${LONG}` })),
    apps: APPS.map((row) => ({ ...row, name: `${row.name} ${LONG}`, sourceRepo: `org/${LONG}` })),
    directMembers: MEMBERS.map((row) => ({
      ...row,
      user: { username: `user-${LONG}`, email: `${LONG}@example.com` },
    })),
  },
};

export const SettingsOpen: Story = {
  args: DETAIL,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(canvas.getByRole("button", { name: /Settings/ }));
    const body = within(canvasElement.ownerDocument.body);
    await expect(await body.findByText("Project settings")).toBeInTheDocument();
  },
};
