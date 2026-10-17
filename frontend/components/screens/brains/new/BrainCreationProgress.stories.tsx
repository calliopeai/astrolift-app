import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, fn, userEvent, within } from "storybook/test";

import { BrainCreationProgress } from "./BrainCreationProgress";
import {
  CREATION,
  CREATION_DEPLOYING,
  CREATION_FAILED,
  CREATION_READY,
} from "./new-brain.fixtures";

const meta: Meta<typeof BrainCreationProgress> = {
  title: "Screens/Brains/New/BrainCreationProgress",
  component: BrainCreationProgress,
  parameters: { layout: "padded" },
  args: {
    creation: CREATION,
    loading: false,
    error: null,
    onRetry: fn(),
    deploy: true,
    failed: null,
    onEdit: fn(),
    onStartOver: fn(),
  },
};
export default meta;

type Story = StoryObj<typeof BrainCreationProgress>;

export const Pending: Story = {
  args: { creation: { ...CREATION, state: "PENDING", commitSha: null } },
};

/** The scaffold commit landed; registration is next. */
export const Scaffolded: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByText("Creating platform-brain")).toBeInTheDocument();
    await expect(canvas.getByText("4f1c2a9")).toBeInTheDocument();
    await expect(canvas.getByText(/leaving this page stops nothing/)).toBeInTheDocument();
  },
};

/** The first deployment, linked while it runs. */
export const Deploying: Story = {
  args: { creation: CREATION_DEPLOYING },
  play: async ({ canvasElement }) => {
    await expect(within(canvasElement).getByRole("link", { name: "d0000000" })).toHaveAttribute(
      "href",
      "/deployments/d0000000-0000-4000-8000-000000000001"
    );
  },
};

/** Done: the layer opens from here. */
export const Ready: Story = {
  args: { creation: CREATION_READY },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByText("Platform knowledge is ready")).toBeInTheDocument();
    await expect(canvas.getByRole("link", { name: /Open semantic layer/ })).toHaveAttribute(
      "href",
      "/brains/platform-brain"
    );
  },
};

/** Created without a deploy: registration is the last step. */
export const ReadyWithoutDeploy: Story = {
  args: { creation: { ...CREATION_READY, deploymentId: null }, deploy: false },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.queryByText("First deploy")).not.toBeInTheDocument();
    await expect(canvas.getByText("Not asked for")).toBeInTheDocument();
  },
};

/** Stopped at the repository: the reason, and the form again with its values. */
export const Failed: Story = {
  args: { creation: CREATION_FAILED, failed: "REPO_READY" },
  play: async ({ args, canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByText(/PRECONDITION: /)).toBeInTheDocument();
    await userEvent.click(canvas.getByRole("button", { name: /Edit and try again/ }));
    await expect(args.onEdit).toHaveBeenCalled();
  },
};

/** Opened from a link after a reload: the values are gone, so only Start over. */
export const FailedAfterReload: Story = {
  args: { creation: CREATION_FAILED, failed: "REPO_READY", onEdit: undefined },
};

/** The first read is in flight. */
export const Loading: Story = { args: { creation: null, loading: true } };

/** Nothing the viewer can see has this id. */
export const Missing: Story = { args: { creation: null } };

export const ReadFailed: Story = { args: { creation: null, error: "Network error" } };

/** A later read failed: what is known stays, polling waits for Retry. */
export const PollFailed: Story = {
  args: { error: "Network error" },
  play: async ({ args, canvasElement }) => {
    await userEvent.click(within(canvasElement).getByRole("button", { name: "Retry" }));
    await expect(args.onRetry).toHaveBeenCalled();
  },
};

export const Width768: Story = {
  args: { creation: CREATION_DEPLOYING },
  render: (args) => (
    <div style={{ width: 768 }}>
      <BrainCreationProgress {...args} />
    </div>
  ),
};
