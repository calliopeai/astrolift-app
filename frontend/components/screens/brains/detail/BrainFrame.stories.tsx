import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { LayersIcon } from "lucide-react";
import { expect, fn, userEvent, within } from "storybook/test";

import { EmptyState } from "@/components/EmptyState";

import { CONNECTION } from "../brains.fixtures";
import { EVERYTHING, NOTHING } from "./brain-detail-model";
import { BRAIN, LONG_BRAIN, NOT_SERVED } from "./brain-detail.fixtures";
import { BrainFrame } from "./BrainFrame";

/** A stand-in for the tab body; each tab has its own stories. */
function Body({ label }: { label: string }) {
  return (
    <EmptyState
      icon={<LayersIcon className="size-5" />}
      title={label}
      description="The tab's own screen renders here."
    />
  );
}

const meta: Meta<typeof BrainFrame> = {
  title: "Screens/Brains/Detail/BrainFrame",
  component: BrainFrame,
  parameters: { layout: "padded" },
  args: {
    slug: BRAIN.slug,
    pathname: `/brains/${BRAIN.slug}`,
    brain: BRAIN,
    loading: false,
    error: null,
    onRetry: fn(),
    archived: null,
    restoring: false,
    onRestore: fn(),
    can: EVERYTHING,
    connecting: false,
    onConnect: fn(),
    connection: null,
    onDismissConnection: fn(),
    children: <Body label="Overview" />,
  },
};
export default meta;

type Story = StoryObj<typeof BrainFrame>;

/** The header, Connect, and the one row of tabs with Overview lit. */
export const Overview: Story = {
  play: async ({ args, canvasElement }) => {
    const canvas = within(canvasElement);
    const tabs = within(canvas.getByRole("navigation", { name: "Semantic layer sections" }));
    await expect(tabs.getAllByRole("link").map((l) => l.textContent)).toEqual([
      "Overview",
      "Substrate",
      "Sharing",
      "Apps",
      "Connect",
      "Settings",
    ]);
    await expect(tabs.getByRole("link", { name: "Overview" })).toHaveAttribute(
      "aria-current",
      "page"
    );
    await expect(tabs.getByRole("link", { name: "Sharing" })).toHaveAttribute(
      "href",
      "/brains/platform-brain/sharing"
    );
    await expect(canvas.getByText("Served in 2 environments")).toBeInTheDocument();
    await userEvent.click(canvas.getByRole("button", { name: "Connect" }));
    await expect(args.onConnect).toHaveBeenCalled();
  },
};

/** The `⋯` menu offers what the viewer may do: rebuild, host, the app, archive. */
export const Menu: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(canvas.getByRole("button", { name: "More actions" }));
    const body = within(canvasElement.ownerDocument.body);
    await expect(await body.findByRole("menuitem", { name: /Archive/ })).toHaveAttribute(
      "href",
      "/brains/platform-brain/settings?section=danger-zone"
    );
    await expect(body.getByRole("menuitem", { name: /Rebuild/ })).toHaveAttribute(
      "href",
      "/brains/platform-brain/settings"
    );
  },
};

/** A viewer who may only read: the menu keeps the app link and nothing else. */
export const ReadOnly: Story = {
  args: { can: NOTHING },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(canvas.getByRole("button", { name: "More actions" }));
    const body = within(canvasElement.ownerDocument.body);
    await expect(await body.findByRole("menuitem", { name: /Open the app/ })).toBeInTheDocument();
    await expect(body.queryByRole("menuitem", { name: /Archive/ })).not.toBeInTheDocument();
    await expect(body.queryByRole("menuitem", { name: /Rebuild/ })).not.toBeInTheDocument();
  },
};

/** Nothing served: Connect waits for an environment with a URL. */
export const NotServed: Story = {
  args: { brain: NOT_SERVED, slug: NOT_SERVED.slug, pathname: `/brains/${NOT_SERVED.slug}` },
  play: async ({ canvasElement }) => {
    await expect(within(canvasElement).getByRole("button", { name: "Connect" })).toBeDisabled();
  },
};

export const SettingsTab: Story = {
  args: { pathname: `/brains/${BRAIN.slug}/settings`, children: <Body label="Settings" /> },
};

/** The token, shown once. */
export const Connected: Story = {
  args: { connection: { ...CONNECTION, slug: BRAIN.slug } },
  play: async ({ canvasElement }) => {
    const body = within(canvasElement.ownerDocument.body);
    await expect(await body.findByText("https://brain.acme.example/mcp")).toBeInTheDocument();
  },
};

export const Loading: Story = { args: { brain: null, loading: true } };

export const LoadFailed: Story = {
  args: { brain: null, error: { message: "Network error", denied: false } },
  play: async ({ args, canvasElement }) => {
    await userEvent.click(within(canvasElement).getByRole("button", { name: "Retry" }));
    await expect(args.onRetry).toHaveBeenCalled();
  },
};

/** Refused by the server: no app.read on this layer. */
export const Denied: Story = {
  args: { brain: null, error: { message: "permission denied: app.read", denied: true } },
};

/** No layer has this slug that the viewer can read; another org's reads the same. */
export const NotFound: Story = { args: { brain: null } };

/** Archived: its page offers Restore (restoreApp). */
export const Archived: Story = {
  args: { brain: null, archived: { name: BRAIN.name, archivedAt: "2026-10-16T08:00:00Z" } },
  play: async ({ args, canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByText("This semantic layer is archived")).toBeInTheDocument();
    await userEvent.click(canvas.getByRole("button", { name: "Restore" }));
    await expect(args.onRestore).toHaveBeenCalled();
  },
};

/** Archived, and the viewer may not restore it: the permission it takes, said. */
export const ArchivedReadOnly: Story = {
  args: { brain: null, archived: { name: BRAIN.name, archivedAt: null }, can: NOTHING },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.queryByRole("button", { name: "Restore" })).not.toBeInTheDocument();
    await expect(canvas.getByText("app.update")).toBeInTheDocument();
  },
};

export const Restoring: Story = {
  args: { brain: null, archived: { name: BRAIN.name, archivedAt: null }, restoring: true },
};

export const LongStrings: Story = { args: { brain: LONG_BRAIN, slug: LONG_BRAIN.slug } };

export const Width768: Story = {
  render: (args) => (
    <div style={{ width: 768 }}>
      <BrainFrame {...args} brain={LONG_BRAIN} slug={LONG_BRAIN.slug} />
    </div>
  ),
};
