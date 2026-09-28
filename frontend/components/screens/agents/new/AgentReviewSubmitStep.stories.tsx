import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import {
  AGENT_REVIEW,
  AGENT_REVIEW_DONE,
  AGENT_REVIEW_EMPTY,
  AGENT_REVIEW_FAILED,
  AGENT_REVIEW_LONG,
  AGENT_REVIEW_RUNNING,
  AGENT_REVIEW_SUBMITTING,
} from "./agents-wizard-b.fixtures";
import { AgentReviewSubmitStepView } from "./AgentReviewSubmitStep";

const meta: Meta<typeof AgentReviewSubmitStepView> = {
  title: "Screens/Agents/New/AgentReviewSubmitStep",
  component: AgentReviewSubmitStepView,
};
export default meta;

type Story = StoryObj<typeof AgentReviewSubmitStepView>;

/** Ready to submit: two new agents, one already registered. */
export const Full: Story = { args: AGENT_REVIEW };

/** No data loading on this step; the closest state is a submit in flight. */
export const Loading: Story = { args: AGENT_REVIEW_SUBMITTING };

/** Register side effect running. */
export const Running: Story = { args: AGENT_REVIEW_RUNNING };

/** No discovered agents and no project label (discovery normally blocks this). */
export const Empty: Story = { args: AGENT_REVIEW_EMPTY };

/** Submit failed. */
export const Failed: Story = { args: AGENT_REVIEW_FAILED };

/** Submit succeeded: per-agent Created / Already existed outcomes. */
export const Done: Story = { args: AGENT_REVIEW_DONE };

export const LongStrings: Story = { args: AGENT_REVIEW_LONG };
