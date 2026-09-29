import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import {
  DEPLOY_IN_FLIGHT,
  DEPLOY_LONG,
  LATEST_DEPLOY,
  LATEST_DEPLOY_FAILED,
} from "./app-overview-panels.fixtures";
import { LatestDeployPanel } from "./LatestDeployPanel";

const meta: Meta<typeof LatestDeployPanel> = {
  title: "Screens/Apps/Overview/LatestDeployPanel",
  component: LatestDeployPanel,
  args: LATEST_DEPLOY,
};
export default meta;

type Story = StoryObj<typeof LatestDeployPanel>;

export const Full: Story = {};

export const Loading: Story = { args: { loading: true, current: null } };

/** Never deployed: the empty state points at the Deployments tab. */
export const Empty: Story = { args: { current: null } };

/**
 * A failed deploy: the reason leads the panel with Roll back beside it. The
 * panel has no query-error state of its own (the stream keeps the last rows).
 */
export const Failed: Story = { args: LATEST_DEPLOY_FAILED };

/** Failed with nothing good to roll back to. */
export const FailedNoRollback: Story = { args: { ...LATEST_DEPLOY_FAILED, lastGood: undefined } };

/** Rolling out: the elapsed time ticks. */
export const InFlight: Story = { args: { current: DEPLOY_IN_FLIGHT } };

/** A 64-char SHA as the tag, a 200-char ARN as the reason, an unbroken URL. */
export const LongStrings: Story = { args: { current: DEPLOY_LONG } };

export const At768: Story = {
  args: { current: DEPLOY_LONG },
  render: (args) => (
    <div style={{ width: 768 }}>
      <LatestDeployPanel {...args} />
    </div>
  ),
};
