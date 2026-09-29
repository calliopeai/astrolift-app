import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";

import { DISABLED_WORKFLOW, LONG, LONG_WORKFLOW, SHELL, TABS } from "./workflow-detail-b.fixtures";
import { WorkflowDetailShellView } from "./WorkflowDetailShell";
import { WorkflowTabsView } from "./WorkflowTabs";

const meta: Meta<typeof WorkflowDetailShellView> = {
  title: "Screens/Workflows/Detail/WorkflowDetailShell",
  component: WorkflowDetailShellView,
  parameters: { layout: "fullscreen" },
  args: {
    ...SHELL,
    tabs: <WorkflowTabsView {...TABS} />,
    children: (
      <div className="text-muted-foreground rounded-md border p-6 text-sm">
        Active pillar content renders here.
      </div>
    ),
  },
};
export default meta;

type Story = StoryObj<typeof WorkflowDetailShellView>;

export const Full: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByText("Nightly sync")).toBeInTheDocument();
    await expect(canvas.getByText("Schedule")).toBeInTheDocument();
    await expect(canvas.getByText("3 runs")).toBeInTheDocument();
  },
};

export const Loading: Story = {
  args: { workflow: null, loading: true },
};

/** No workflow with this slug; the shell's empty state. */
export const NotFound: Story = {
  args: { workflowSlug: "no-such-workflow", workflow: null },
};

export const LoadFailed: Story = {
  args: { workflow: null, error: { message: "Network error: failed to fetch" } },
};

/** Disabled, manual trigger, never run. */
export const DisabledNeverRun: Story = {
  args: { workflow: DISABLED_WORKFLOW },
};

export const LongStrings: Story = {
  args: {
    workflowSlug: LONG,
    workflow: LONG_WORKFLOW,
    tabs: <WorkflowTabsView workflowSlug={LONG} pathname={`/workflows/${LONG}/run`} />,
  },
};
