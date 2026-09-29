import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { ModelReplicasView } from "./ModelReplicas";
import { LONG, REPLICAS } from "./models-providers.fixtures";

const meta: Meta<typeof ModelReplicasView> = {
  title: "Screens/Models/ModelReplicas",
  component: ModelReplicasView,
  args: REPLICAS,
};
export default meta;

type Story = StoryObj<typeof ModelReplicasView>;

export const Running: Story = {};

export const Scaled: Story = { args: { replicas: 3 } };

export const AtMax: Story = { args: { replicas: 8 } };

/** Replicas 0: the toggle reads Start. There is no empty state; this is the closest. */
export const Stopped: Story = { args: { replicas: 0 } };

/** A mutation in flight: every control is disabled. */
export const Updating: Story = { args: { loading: true } };

export const LongName: Story = { args: { name: LONG } };
