import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { DeployModelSheetView } from "./DeployModelSheet";
import { DEPLOY, LONG } from "./models-providers.fixtures";

const meta: Meta<typeof DeployModelSheetView> = {
  title: "Screens/Models/DeployModelSheet",
  component: DeployModelSheetView,
  args: DEPLOY,
};
export default meta;

type Story = StoryObj<typeof DeployModelSheetView>;

/** Nothing chosen yet: Deploy stays disabled until an environment is picked. */
export const Full: Story = {};

/** Environment on a probed GPU cluster: the fit line says it fits. */
export const Fits: Story = { args: { initialEnvId: "e1" } };

/** Cluster never probed: GPU memory unknown, deploy still allowed. */
export const UnknownGpu: Story = { args: { initialEnvId: "e2" } };

/**
 * The sheet shows no loading or error state of its own (targets load behind
 * an empty picker); this is the closest: no environments yet.
 */
export const NoEnvironments: Story = { args: { envs: [], clusters: [] } };

export const Deploying: Story = { args: { initialEnvId: "e1", loading: true } };

export const LongStrings: Story = {
  args: {
    initialEnvId: "el",
    envs: [
      { id: "el", name: LONG, registeredAppSlug: LONG, clusterId: "c1", clusterSlug: LONG },
      ...DEPLOY.envs,
    ],
  },
};
