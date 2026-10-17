import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, fn, userEvent, within } from "storybook/test";

import type { BrainStatus } from "../brain-status-model";
import {
  BEHIND,
  DEPLOY_FAILED,
  DEPLOYING,
  HEALTHY,
  NOT_DEPLOYED,
  UNKNOWN_VALUES,
} from "../brain-status.fixtures";
import type { BrainStatusResult } from "../BrainStatusCell";
import { BRAIN, NOT_SERVED, ONE_ENV } from "./brain-detail.fixtures";
import { BrainOverviewTab } from "./BrainOverviewTab";

const ready = (status: BrainStatus | null): BrainStatusResult => ({ state: "ready", status });

const ENVIRONMENTS = [
  { name: "production", href: "/brains/platform-brain?env=production", active: true },
  { name: "staging", href: "/brains/platform-brain?env=staging", active: false },
];

const meta: Meta<typeof BrainOverviewTab> = {
  title: "Screens/Brains/Detail/BrainOverviewTab",
  component: BrainOverviewTab,
  parameters: { layout: "padded" },
  args: {
    brain: BRAIN,
    environment: "production",
    environments: ENVIRONMENTS,
    status: ready(HEALTHY),
    onRetry: fn(),
  },
};
export default meta;

type Story = StoryObj<typeof BrainOverviewTab>;

/** Running at the branch head, compiled and ingested, served in two environments. */
export const Healthy: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByText("At head")).toBeInTheDocument();
    await expect(canvas.getByRole("link", { name: "staging" })).toHaveAttribute(
      "href",
      "/brains/platform-brain?env=staging"
    );
    await expect(
      canvas.getByText("MCP endpoint: https://platform-brain.apps.example.test/mcp")
    ).toBeInTheDocument();
  },
};

/** The branch moved on, the last ingest failed, and a host read failed: listed first. */
export const BehindWithWarnings: Story = {
  args: { status: ready(BEHIND) },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByText("1 host read failed")).toBeInTheDocument();
    await expect(canvas.getByText("Behind head")).toBeInTheDocument();
    await expect(canvas.getByText("Not compiled at the running commit")).toBeInTheDocument();
  },
};

/** The latest deploy failed: its reason leads the Health panel. */
export const DeployFailed: Story = {
  args: { status: ready(DEPLOY_FAILED) },
  play: async ({ canvasElement }) => {
    await expect(within(canvasElement).getByText("The latest deploy failed")).toBeInTheDocument();
  },
};

export const Deploying: Story = { args: { status: ready(DEPLOYING) } };
export const NotDeployed: Story = { args: { status: ready(NOT_DEPLOYED) } };

/** The status names a value this build does not know: shown as itself. */
export const UnknownStatus: Story = { args: { status: ready(UNKNOWN_VALUES) } };

/** One environment: no picker. */
export const OneEnvironment: Story = {
  args: { brain: ONE_ENV, environments: ENVIRONMENTS.slice(0, 1) },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).queryByRole("navigation", { name: "Environments" })
    ).not.toBeInTheDocument();
  },
};

/** The status is on its way; the endpoints come with the layer and show already. */
export const Loading: Story = {
  args: { status: { state: "loading" } },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByText("https://platform-brain.apps.example.test")
    ).toBeInTheDocument();
  },
};

/** The status read failed: each status panel offers Retry, the endpoints still show. */
export const StatusFailed: Story = {
  args: { status: { state: "error", message: "source head: the host answered 502" } },
  play: async ({ args, canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(canvas.getAllByRole("button", { name: "Retry" })[0]!);
    await expect(args.onRetry).toHaveBeenCalled();
    await expect(canvas.getByText("https://platform-brain.apps.example.test")).toBeInTheDocument();
  },
};

/** The picked environment does not exist (any more). */
export const NoSuchEnvironment: Story = { args: { status: ready(null), environment: "qa" } };

/** Registered, nothing served yet. */
export const NotServed: Story = {
  args: { brain: NOT_SERVED, environments: [], status: ready(NOT_DEPLOYED) },
};

export const Width768: Story = {
  args: { status: ready(BEHIND) },
  render: (args) => (
    <div style={{ width: 768 }}>
      <BrainOverviewTab {...args} />
    </div>
  ),
};
