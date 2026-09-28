import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import {
  REVIEW,
  REVIEW_FAILED,
  REVIEW_LONG,
  REVIEW_MINIMAL,
  REVIEW_RUNNING,
  REVIEW_SUBMITTING,
} from "./apps-wizard-steps-b.fixtures";
import { ReviewSubmitStepView } from "./ReviewSubmitStep";

const meta: Meta<typeof ReviewSubmitStepView> = {
  title: "Screens/Apps/New/ReviewSubmitStep",
  component: ReviewSubmitStepView,
};
export default meta;

type Story = StoryObj<typeof ReviewSubmitStepView>;

/** Every step filled, nothing submitted yet. */
export const Full: Story = { args: REVIEW };

/** Submit clicked before the side-effect plan exists. */
export const Loading: Story = { args: REVIEW_SUBMITTING };

/** Side effects running. */
export const Running: Story = { args: REVIEW_RUNNING };

/** The step has no empty state; the closest is minimal inputs with blank fields. */
export const Empty: Story = { args: REVIEW_MINIMAL };

/** Submit failed and a side effect failed. */
export const SubmitFailed: Story = { args: REVIEW_FAILED };

export const LongStrings: Story = { args: REVIEW_LONG };
