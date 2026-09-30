import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";

import {
  AGENT_REPO_PICKER,
  AGENT_REPO_PICKER_LOADING,
  AGENT_REPO_PICKER_LONG,
  AGENT_REPO_PICKER_NO_CONNECTIONS,
  AGENT_REPO_PICKER_NO_REPOS,
  AGENT_REPO_PICKER_REPOS_ERROR,
  AGENT_REPO_PICKER_REPOS_LOADING,
  AGENT_REPO_PICKER_UNPICKED,
} from "./agents-wizard-b.fixtures";
import { AgentRepoPickerStepView } from "./AgentRepoPickerStep";

const meta: Meta<typeof AgentRepoPickerStepView> = {
  title: "Screens/Agents/New/AgentRepoPickerStep",
  component: AgentRepoPickerStepView,
};
export default meta;

type Story = StoryObj<typeof AgentRepoPickerStepView>;

/** Connection and repo picked; the branch / ref input shows. */
export const Full: Story = {
  args: AGENT_REPO_PICKER,
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByRole("link", { name: "Missing a host? Connect another source →" })
    ).toHaveAttribute("href", "/providers#source");
  },
};

/** Source connections still loading. */
export const Loading: Story = { args: AGENT_REPO_PICKER_LOADING };

/** No usable source connection. */
export const Empty: Story = { args: AGENT_REPO_PICKER_NO_CONNECTIONS };

/** Several connections, none picked yet. */
export const Unpicked: Story = { args: AGENT_REPO_PICKER_UNPICKED };

export const ReposLoading: Story = { args: AGENT_REPO_PICKER_REPOS_LOADING };

export const NoRepos: Story = { args: AGENT_REPO_PICKER_NO_REPOS };

/** The host refused the token; recoverable, so the reauth action shows. */
export const ReposFailed: Story = { args: AGENT_REPO_PICKER_REPOS_ERROR };

export const LongStrings: Story = { args: AGENT_REPO_PICKER_LONG };
