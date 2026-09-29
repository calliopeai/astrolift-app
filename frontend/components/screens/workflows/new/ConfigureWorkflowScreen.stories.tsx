import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";

import { ConfigureWorkflowScreen } from "./ConfigureWorkflowScreen";
import { CONFIGURE, LONG_CONFIGURE, UNBOUND_STAGES } from "./workflows-new-builder.fixtures";

const meta: Meta = {
  title: "Screens/Workflows/New/ConfigureWorkflowScreen",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

/** Step 1, Source: the template, and the workflow's name, trigger and inputs. */
export const Full: Story = { render: () => <ConfigureWorkflowScreen {...CONFIGURE} /> };

export const Loading: Story = {
  render: () => <ConfigureWorkflowScreen {...CONFIGURE} definition={null} loading />,
};

/** No definition with this slug, or no access to it. */
export const NotFound: Story = {
  render: () => (
    <ConfigureWorkflowScreen {...CONFIGURE} definitionSlug="no-such-template" definition={null} />
  ),
};

export const LoadFailed: Story = {
  render: () => (
    <ConfigureWorkflowScreen
      {...CONFIGURE}
      definition={null}
      error={new Error("Network error: failed to fetch")}
    />
  ),
};

export const NoPermission: Story = {
  render: () => <ConfigureWorkflowScreen {...CONFIGURE} canCreate={false} />,
};

/** Continue without a name, a cron or valid inputs: each error beside its field. */
export const SourceErrors: Story = {
  render: () => (
    <ConfigureWorkflowScreen
      {...CONFIGURE}
      initialValues={{ triggerKind: "schedule", inputsText: "{ not json" }}
    />
  ),
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(canvas.getByRole("button", { name: /Continue/ }));
    await expect(await canvas.findByText("Give the workflow a name.")).toBeInTheDocument();
    await expect(canvas.getByText("A schedule needs a cron expression.")).toBeInTheDocument();
    await expect(canvas.getByText("Inputs must be valid JSON.")).toBeInTheDocument();
  },
};

/** Step 2, Stages: an agent picker per agent stage. */
export const Stages: Story = {
  render: () => (
    <ConfigureWorkflowScreen {...CONFIGURE} initialStep={2} initialValues={{ name: "Prod" }} />
  ),
};

/** The template has no stages yet (the closest real empty state). */
export const NoStages: Story = {
  render: () => <ConfigureWorkflowScreen {...CONFIGURE} orderedStages={[]} initialStep={2} />,
};

/** An agent stage without a default agent holds the page on Stages until one is bound. */
export const UnboundStage: Story = {
  render: () => (
    <ConfigureWorkflowScreen
      {...CONFIGURE}
      orderedStages={UNBOUND_STAGES}
      initialStep={2}
      initialValues={{ name: "Prod" }}
    />
  ),
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(canvas.getByRole("button", { name: /Continue/ }));
    await expect(
      await canvas.findByText("Bind an agent: this stage has no default.")
    ).toBeInTheDocument();
  },
};

/** Step 3, Review: what will be created. */
export const Review: Story = {
  render: () => (
    <ConfigureWorkflowScreen
      {...CONFIGURE}
      initialStep={3}
      initialValues={{
        name: "Outbound, production",
        triggerKind: "schedule",
        scheduleCron: "0 9 * * 1-5",
        inputsText: '{ "region": "us-west-2" }',
      }}
    />
  ),
};

export const Submitting: Story = {
  render: () => (
    <ConfigureWorkflowScreen
      {...CONFIGURE}
      submitting
      initialStep={3}
      initialValues={{ name: "Prod" }}
    />
  ),
};

/** Refused for a reason no field owns. */
export const CreateFailed: Story = {
  render: () => (
    <ConfigureWorkflowScreen
      {...CONFIGURE}
      initialStep={3}
      initialValues={{ name: "Prod" }}
      initialErrors={{ form: "definition_slug: this template was archived" }}
    />
  ),
};

export const LongStrings: Story = {
  render: () => <ConfigureWorkflowScreen {...LONG_CONFIGURE} />,
};

export const LongStringsStages: Story = {
  render: () => <ConfigureWorkflowScreen {...LONG_CONFIGURE} initialStep={2} />,
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <ConfigureWorkflowScreen {...LONG_CONFIGURE} initialStep={2} />
    </div>
  ),
};
