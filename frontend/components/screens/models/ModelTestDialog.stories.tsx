import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { ModelTestDialogView } from "./ModelTestDialog";
import { LONG, TEST_DIALOG, TEST_REPLY } from "./models-providers.fixtures";

const meta: Meta<typeof ModelTestDialogView> = {
  title: "Screens/Models/ModelTestDialog",
  component: ModelTestDialogView,
  args: { ...TEST_DIALOG, defaultOpen: true },
};
export default meta;

type Story = StoryObj<typeof ModelTestDialogView>;

/** Closed: just the Test button in the row. */
export const Closed: Story = { args: { defaultOpen: false } };

/** Open with nothing sent yet (the closest thing to an empty state). */
export const Idle: Story = {};

export const Waiting: Story = { args: { loading: true } };

export const Reply: Story = { args: { outcome: TEST_REPLY } };

export const TestError: Story = {
  args: {
    outcome: {
      kind: "error",
      message:
        "this cluster has no connected agent; deploy the keep-alive agent from the cluster's settings and wait for it to report healthy before testing a model",
    },
  },
};

export const TimedOut: Story = {
  args: {
    outcome: {
      kind: "reply",
      status: "timed_out",
      reply: "",
      latencyMs: null,
      promptTokens: null,
      completionTokens: null,
      totalTokens: null,
      error: "the cluster agent did not respond in time",
    },
  },
};

export const LongStrings: Story = {
  args: {
    name: LONG,
    outcome: { ...TEST_REPLY, reply: `${LONG} `.repeat(12), latencyMs: 123456789 },
  },
};
