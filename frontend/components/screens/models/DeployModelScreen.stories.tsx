import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";

import { DeployModelScreen } from "./DeployModelScreen";
import { DEPLOY, LONG } from "./models-providers.fixtures";

const meta: Meta<typeof DeployModelScreen> = {
  title: "Screens/Models/DeployModelScreen",
  component: DeployModelScreen,
  args: DEPLOY,
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj<typeof DeployModelScreen>;

/** Nothing chosen yet: step 1, where it runs. */
export const Where: Story = {};

export const TargetsLoading: Story = { args: { envs: [], envsLoading: true } };

/** No environments to deploy to. */
export const NoEnvironments: Story = { args: { envs: [], clusters: [] } };

export const TargetsError: Story = {
  args: { envs: [], envsError: "Response not successful: Received status code 500" },
};

/** Environment on a probed GPU cluster: the fit line says it fits. */
export const Model: Story = { args: { initialStep: 2, initialEnvId: "e1" } };

/** Cluster never probed: GPU memory unknown, deploy still allowed. */
export const UnknownGpu: Story = { args: { initialStep: 2, initialEnvId: "e2" } };

/** What will be provisioned, before it costs anything. */
export const Review: Story = { args: { initialStep: 3, initialEnvId: "e1" } };

export const Deploying: Story = { args: { initialStep: 3, initialEnvId: "e1", loading: true } };

/** The server refused: the reason leads the review. */
export const Refused: Story = {
  args: {
    initialStep: 3,
    initialEnvId: "e1",
    initialError: "Managed service qwen3-8b already exists on chat · prod.",
  },
};

/** Continue with no environment: the error appears in place, not in a toast. */
export const ValidatesInPlace: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(canvas.getByRole("button", { name: /Continue/ }));
    await expect(
      await canvas.findByText("Choose the app environment the model runs in.")
    ).toBeInTheDocument();
  },
};

export const LongStrings: Story = {
  args: {
    initialStep: 3,
    initialEnvId: "el",
    envs: [
      { id: "el", name: LONG, registeredAppSlug: LONG, clusterId: "c1", clusterSlug: LONG },
      ...DEPLOY.envs,
    ],
  },
};

export const Width768: Story = {
  args: LongStrings.args,
  render: (args) => (
    <div style={{ width: 768 }}>
      <DeployModelScreen {...args} />
    </div>
  ),
};

export const CapabilitiesFailed: Story = {
  args: { clusters: [], clustersError: "Could not read cluster GPU capabilities" },
};
export const CapabilitiesLoading: Story = { args: { clusters: [], clustersLoading: true } };
