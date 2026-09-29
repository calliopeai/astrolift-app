import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import {
  REPO_PICKER,
  REPO_PICKER_LOADING,
  REPO_PICKER_LONG,
  REPO_PICKER_NO_CLUSTER,
  REPO_PICKER_NO_CONNECTIONS,
  REPO_PICKER_NO_REPOS,
  REPO_PICKER_REPOS_ERROR,
  REPO_PICKER_REPOS_LOADING,
  REPO_PICKER_UNPICKED,
} from "./apps-wizard-steps-b.fixtures";
import { RepoPickerStepView } from "./RepoPickerStep";

const meta: Meta<typeof RepoPickerStepView> = {
  title: "Screens/Apps/New/RepoPickerStep",
  component: RepoPickerStepView,
};
export default meta;

type Story = StoryObj<typeof RepoPickerStepView>;

/** Connection and repo picked. */
export const Full: Story = { args: REPO_PICKER };

/** Cluster preflight in flight. */
export const Loading: Story = { args: REPO_PICKER_LOADING };

/** No managed cluster: the step is gated before any connection shows. */
export const NoCluster: Story = { args: REPO_PICKER_NO_CLUSTER };

/** Cluster present, no usable source connection. */
export const Empty: Story = { args: REPO_PICKER_NO_CONNECTIONS };

/** Several connections, none picked yet. */
export const Unpicked: Story = { args: REPO_PICKER_UNPICKED };

export const ReposLoading: Story = { args: REPO_PICKER_REPOS_LOADING };

export const NoRepos: Story = { args: REPO_PICKER_NO_REPOS };

/** The host refused the token; recoverable, so the reauth action shows. */
export const ReposFailed: Story = { args: REPO_PICKER_REPOS_ERROR };

export const LongStrings: Story = { args: REPO_PICKER_LONG };
