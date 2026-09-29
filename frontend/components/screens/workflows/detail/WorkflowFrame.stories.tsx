import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";

import { LayersIcon } from "lucide-react";

import { EmptyState } from "@/components/EmptyState";

import {
  DEFINITION_SUBJECT,
  DISABLED_SUBJECT,
  FAILING_FRAME,
  FRAME,
  LONG,
  LONG_FRAME,
  RUNNING_SUBJECT,
  SUBJECT,
} from "./workflow-frame.fixtures";
import { WorkflowFrame } from "./WorkflowFrame";

/**
 * The frame around every workflow page (spec 44 §4.4, §5.2). The body here is
 * a stand-in; each tab's own screen has its own stories.
 */
function Body({ label }: { label: string }) {
  return (
    <EmptyState
      icon={<LayersIcon className="size-5" />}
      title={label}
      description="The tab's own screen renders here."
    />
  );
}

const meta: Meta<typeof WorkflowFrame> = {
  title: "Screens/Workflows/Detail/WorkflowFrame",
  component: WorkflowFrame,
  parameters: { layout: "padded" },
  args: { ...FRAME, children: <Body label="Builder" /> },
};
export default meta;

type Story = StoryObj<typeof WorkflowFrame>;

const at = (path: string, label: string): Story => ({
  args: { pathname: `/workflows/${SUBJECT.slug}${path}`, children: <Body label={label} /> },
  play: async ({ canvasElement }) => {
    const tabs = within(
      within(canvasElement).getByRole("navigation", { name: "Workflow sections" })
    );
    await expect(tabs.getByRole("link", { name: label })).toHaveAttribute("aria-current", "page");
  },
});

/** The default tab, at `/workflows/<slug>`. */
export const Builder: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    const tabs = within(canvas.getByRole("navigation", { name: "Workflow sections" }));
    await expect(tabs.getAllByRole("link").map((l) => l.textContent)).toEqual([
      "Builder",
      "Runs",
      "Triggers",
      "Settings",
    ]);
    await expect(tabs.getByRole("link", { name: "Builder" })).toHaveAttribute(
      "aria-current",
      "page"
    );
    const crumbs = within(canvas.getByRole("navigation", { name: "Breadcrumb" }));
    await expect(crumbs.getByRole("link", { name: "Workflows" })).toHaveAttribute(
      "href",
      "/workflows"
    );
    await expect(canvas.getByText("Last run succeeded")).toBeInTheDocument();
    await expect(canvas.getByText("4 stages")).toBeInTheDocument();
    await expect(canvas.getByRole("link", { name: "revenue-ops" })).toHaveAttribute(
      "href",
      "/projects/revenue-ops"
    );
    await expect(canvas.getByRole("button", { name: /Run/ })).toBeEnabled();
  },
};
export const Runs = at("/runs", "Runs");
export const Triggers = at("/triggers", "Triggers");
export const Settings = at("/settings", "Settings");

/** A former pillar route (`/observe`) still lights the tab that absorbed it. */
export const FormerRoute = at("/observe", "Runs");

/** The last run failed: the reason is the first thing under the header, on every tab. */
export const Failing: Story = {
  args: FAILING_FRAME,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByRole("alert")).toHaveTextContent("AccessDenied");
    await expect(canvas.getByText("Last run failed", { selector: "span" })).toBeInTheDocument();
    await expect(canvas.getByRole("link", { name: "Open run" })).toHaveAttribute(
      "href",
      "/workflows/nightly-sync/runs"
    );
  },
};

/** A failed run that reported no reason says so rather than going blank. */
export const FailingWithoutReason: Story = {
  args: { ...FAILING_FRAME, failedRun: { href: "/workflows/nightly-sync/runs", reason: null } },
};

/** No run grant: no Run. The `⋯` menu is still there. */
export const WithoutRunPermission: Story = {
  args: { canRun: false, canDelete: false },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.queryByRole("button", { name: /Run/ })).toBeNull();
    await expect(canvas.getByRole("button", { name: "More actions" })).toBeInTheDocument();
  },
};

export const Running: Story = { args: { workflow: RUNNING_SUBJECT } };

/** Disabled and never run: Run shows, but cannot start it. */
export const DisabledNeverRun: Story = {
  args: { workflow: DISABLED_SUBJECT },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByText("Disabled")).toBeInTheDocument();
    await expect(canvas.getByRole("button", { name: /Run/ })).toBeDisabled();
  },
};

/** A definition opened directly: one stage, no project, never run. */
export const Definition: Story = {
  args: {
    slug: DEFINITION_SUBJECT.slug,
    pathname: "/workflows/outbound-pipeline",
    workflow: DEFINITION_SUBJECT,
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByText("1 stage")).toBeInTheDocument();
    await expect(canvas.getByText("Never run")).toBeInTheDocument();
  },
};

/** The definition behind a configured workflow has not loaded: no stage count yet. */
export const StagesNotLoaded: Story = {
  args: { workflow: { ...SUBJECT, stageCount: null, projectSlug: null } },
};

export const Dispatching: Story = { args: { dispatching: true } };

export const Loading: Story = { args: { workflow: null, loading: true } };

export const NotFound: Story = { args: { workflow: null, slug: "no-such-workflow" } };

export const LoadError: Story = {
  args: {
    workflow: null,
    error:
      "Network error: request to https://api.astrolift.example.com/graphql/with/an/unbroken/path/that/keeps/going failed",
  },
};

/** A 110-character name and slug, a long pattern kind, a long project. */
export const LongName: Story = { args: LONG_FRAME };

/** The narrowest the web console goes: no horizontal page scroll. */
export const At768: Story = {
  args: { ...LONG_FRAME, failedRun: FAILING_FRAME.failedRun, slug: LONG },
  render: (args) => (
    <div style={{ width: 768 }}>
      <WorkflowFrame {...args} />
    </div>
  ),
};
