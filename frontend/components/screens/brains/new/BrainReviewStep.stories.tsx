import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, fn, userEvent, within } from "storybook/test";

import { BrainReviewStep } from "./BrainReviewStep";
import { CONNECTIONS, DRAFT, POSTGRES_DRAFT } from "./new-brain.fixtures";

const meta: Meta<typeof BrainReviewStep> = {
  title: "Screens/Brains/New/BrainReviewStep",
  component: BrainReviewStep,
  parameters: { layout: "padded" },
  args: {
    draft: DRAFT,
    connectionName: CONNECTIONS[0]!.name,
    teamSlug: "platform",
    projectSlug: "core",
    canDeploy: true,
    onEdit: fn(),
  },
};
export default meta;

type Story = StoryObj<typeof BrainReviewStep>;

/** The whole request; production deploys ask for a fresh sign-in, and it says so. */
export const FilesCell: Story = {
  play: async ({ args, canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByText("acme-platform/platform-brain")).toBeInTheDocument();
    await expect(canvas.getByText("The edition this install pins")).toBeInTheDocument();
    await expect(canvas.getByText(/sign in again/)).toBeInTheDocument();
    await userEvent.click(canvas.getByRole("button", { name: "Edit Substrate" }));
    await expect(args.onEdit).toHaveBeenCalledWith(3);
  },
};

export const PostgresCell: Story = {
  args: { draft: { ...POSTGRES_DRAFT, templateRef: "v1.4.0" } },
  play: async ({ canvasElement }) => {
    await expect(within(canvasElement).getByText("postgres, s3")).toBeInTheDocument();
  },
};

/** No first deploy: the steps stop at registration. */
export const NoDeploy: Story = {
  args: { draft: { ...DRAFT, deploy: false } },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByText("No, deploy it later")).toBeInTheDocument();
    await expect(canvas.queryByText(/Deploys that commit/)).not.toBeInTheDocument();
  },
};

export const Staging: Story = { args: { draft: { ...DRAFT, environmentName: "staging" } } };

/** A refusal the server tied to no field leads the step. */
export const Refused: Story = {
  args: { serverError: "the workflow engine is off on this install" },
};
