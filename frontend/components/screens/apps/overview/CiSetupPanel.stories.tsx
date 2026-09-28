import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { CI, CI_APP, CI_LONG, CI_PROBLEMS } from "./app-overview-panels.fixtures";
import { CiSetupPanel } from "./CiSetupPanel";

const meta: Meta<typeof CiSetupPanel> = {
  title: "Screens/Apps/Overview/CiSetupPanel",
  component: CiSetupPanel,
  args: CI,
};
export default meta;

type Story = StoryObj<typeof CiSetupPanel>;

/** Fully wired: workflow matches, webhook installed, secret set. */
export const Full: Story = {};

/**
 * Reads the app record the overview already holds, so it has no loading or
 * query-error state; a repo with every step broken is the closest trouble.
 */
export const Problems: Story = { args: CI_PROBLEMS };

/** Not yet checked: no autowire status or workflow sync on the record. */
export const Unchecked: Story = {
  args: {
    app: {
      ...CI_APP,
      autowire: null,
      ciWorkflowSyncStatus: null,
      sourceWebhookInstalledAt: null,
    },
  },
};

/** No source repo: nothing to wire. */
export const Empty: Story = { args: { app: { ...CI_APP, sourceRepo: "" } } };

export const LongStrings: Story = { args: CI_LONG };

export const At768: Story = {
  args: CI_LONG,
  render: (args) => (
    <div style={{ width: 768 }}>
      <CiSetupPanel {...args} />
    </div>
  ),
};
