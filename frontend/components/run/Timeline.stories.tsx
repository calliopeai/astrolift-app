import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";

import { FAILED_STEPS, LONG_STEPS, RUNNING_STEPS } from "./fixtures";
import { activitiesToSteps, Timeline } from "./Timeline";

/** A run's steps (spec 44 §5.5): pending, running, ok, failed, skipped. */
const meta: Meta = { title: "Run/Timeline", parameters: { layout: "padded" } };
export default meta;

type Story = StoryObj;

export const Running: Story = { render: () => <Timeline steps={RUNNING_STEPS} /> };
export const Failed: Story = { render: () => <Timeline steps={FAILED_STEPS} /> };

/** With `onSelect` each step is a button; the page narrows its log to it. */
export const Selectable: Story = {
  render: () => {
    function Pick() {
      const [id, setId] = React.useState<string | null>("transform");
      return <Timeline steps={RUNNING_STEPS} selectedId={id} onSelect={setId} />;
    }
    return <Pick />;
  },
};

/** The observability WorkflowTimeline's activities, through the adapter. */
export const FromWorkflowActivities: Story = {
  render: () => (
    <Timeline
      steps={activitiesToSteps([
        {
          id: "a1",
          name: "provision",
          status: "succeeded",
          durationMs: 4200,
          attempts: [{ attemptNumber: 1, startedAt: "2026-09-28T12:00:00Z", status: "succeeded" }],
        },
        {
          id: "a2",
          name: "apply",
          status: "failed",
          durationMs: 61_000,
          attempts: [
            {
              attemptNumber: 1,
              startedAt: "2026-09-28T12:00:05Z",
              status: "failed",
              errorMessage: "timeout",
            },
            {
              attemptNumber: 2,
              startedAt: "2026-09-28T12:00:40Z",
              status: "failed",
              errorMessage: "helm upgrade failed: context deadline exceeded",
            },
          ],
        },
        { id: "a3", name: "verify", status: "cancelled", attempts: [] },
      ])}
    />
  ),
};

export const Empty: Story = { render: () => <Timeline steps={[]} /> };
export const LongStrings: Story = { render: () => <Timeline steps={LONG_STEPS} /> };

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <Timeline steps={[...FAILED_STEPS, ...LONG_STEPS]} />
    </div>
  ),
};
