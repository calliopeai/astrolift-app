import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { SCALING, SCALING_HPA } from "./app-workloads.fixtures";
import { ScalingCardView } from "./ScalingCard";

const meta: Meta<typeof ScalingCardView> = {
  title: "Screens/Apps/Workloads/ScalingCard",
  component: ScalingCardView,
  args: SCALING,
};
export default meta;

type Story = StoryObj<typeof ScalingCardView>;

export const Full: Story = {};

/** HPA on, scaling toward its ceiling. */
export const HpaScaling: Story = { args: SCALING_HPA };

export const Loading: Story = { args: { status: null, loading: true } };

/**
 * The card renders nothing when the status resolver returns null (its
 * only empty or error outcome); the closest visible state is zero replicas.
 */
export const ScaledToZero: Story = {
  args: { status: { ...SCALING.status!, currentReplicas: 0, desiredReplicas: 0 } },
};

/** Without app.deploy the slider and Apply are disabled. */
export const NoPermission: Story = { args: { canDeploy: false } };

export const Applying: Story = { args: { scaling: true } };

/** No free text on this card; the widest real values are three-digit bounds. */
export const LongStrings: Story = {
  args: {
    status: {
      ...SCALING_HPA.status!,
      replicaUpperBound: 500,
      hpaMaxReplicas: 500,
      currentReplicas: 499,
      desiredReplicas: 500,
    },
  },
};

/** The narrowest the web console goes (spec 44 §6). */
export const Width768: Story = {
  args: SCALING_HPA,
  decorators: [
    (Story) => (
      <div style={{ width: 768 }}>
        <Story />
      </div>
    ),
  ],
};
