import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, fn, userEvent, within } from "storybook/test";

import { DEFAULT_ASK } from "./brain-detail-model";
import { BrainSettingsTab, type BrainSettingsTabProps } from "./BrainSettingsTab";

const SECTIONS = [
  { id: "rebuild", label: "Rebuild", href: "/brains/platform-brain/settings" },
  {
    id: "danger-zone",
    label: "Danger zone",
    href: "/brains/platform-brain/settings?section=danger-zone",
  },
];

const REBUILD: BrainSettingsTabProps["rebuild"] = {
  environments: ["production", "staging"],
  environment: "production",
  onEnvironment: fn(),
  ingest: false,
  onIngest: fn(),
  submitting: false,
  onRebuild: fn(),
  result: null,
  error: null,
};

const ARCHIVE: BrainSettingsTabProps["archive"] = {
  ask: DEFAULT_ASK,
  submitting: false,
  onArchive: fn(async () => true),
  error: null,
};

const meta: Meta<typeof BrainSettingsTab> = {
  title: "Screens/Brains/Detail/BrainSettingsTab",
  component: BrainSettingsTab,
  parameters: { layout: "padded" },
  args: {
    layerName: "Platform knowledge",
    section: "rebuild",
    sections: SECTIONS,
    canRebuild: true,
    canArchive: true,
    rebuild: REBUILD,
    archive: ARCHIVE,
  },
};
export default meta;

type Story = StoryObj<typeof BrainSettingsTab>;

/** Rebuild in production says it asks for a fresh sign-in. */
export const Rebuild: Story = {
  play: async ({ args, canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByText(/sign in again/)).toBeInTheDocument();
    await userEvent.click(canvas.getByRole("button", { name: "Rebuild" }));
    await expect(args.rebuild.onRebuild).toHaveBeenCalled();
  },
};

export const WithIngest: Story = {
  args: { rebuild: { ...REBUILD, ingest: true, environment: "staging" } },
};

/** What the deploy started, and what it rebuilds. */
export const Started: Story = {
  args: {
    rebuild: {
      ...REBUILD,
      result: {
        slug: "platform-brain",
        mode: "deploy",
        deploymentId: "d0000000-0000-4000-8000-000000000009",
        runUrl: null,
        ingestRunUrl: null,
        rebuilds: [
          "the projections the brain rebuilds at deploy",
          "app/brain.db, rebuilt by each of 3 replica(s) on its own /data claim",
        ],
      },
    },
  },
  play: async ({ canvasElement }) => {
    await expect(within(canvasElement).getByRole("link", { name: "d0000000" })).toHaveAttribute(
      "href",
      "/deployments/d0000000-0000-4000-8000-000000000009"
    );
  },
};

export const IngestStarted: Story = {
  args: {
    rebuild: {
      ...REBUILD,
      ingest: true,
      result: {
        slug: "platform-brain",
        mode: "ingest",
        deploymentId: null,
        runUrl: "https://github.example.test/acme/platform-brain/actions/runs/102",
        ingestRunUrl: "https://github.example.test/acme/platform-brain/actions/runs/102",
        rebuilds: ["the projections the brain rebuilds at deploy"],
      },
    },
  },
};

/** The repository has no knowledge-refresh workflow: the server's reason. */
export const IngestRefused: Story = {
  args: {
    rebuild: {
      ...REBUILD,
      ingest: true,
      error: {
        message: "the repository has no .github/workflows/knowledge-refresh.yml",
        approval: false,
      },
    },
  },
};

/** Without app.deploy: the form, disabled, with the permission it takes. */
export const RebuildReadOnly: Story = { args: { canRebuild: false } };

/** Archive confirms first, and says what the gate asks. */
export const DangerZone: Story = {
  args: { section: "danger-zone" },
  play: async ({ args, canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(canvas.getByRole("button", { name: "Archive" }));
    const body = within(canvasElement.ownerDocument.body);
    const dialog = within(await body.findByRole("alertdialog"));
    await expect(dialog.getByText("Archive Platform knowledge?")).toBeInTheDocument();
    await userEvent.click(dialog.getByRole("button", { name: "Archive" }));
    await expect(args.archive.onArchive).toHaveBeenCalled();
  },
};

/** An org rule adds approval; the request is linked once asked for. */
export const ApprovalRequested: Story = {
  args: {
    section: "danger-zone",
    archive: {
      ...ARCHIVE,
      ask: { ...DEFAULT_ASK, approval: true, minimumApprovals: 2 },
      error: {
        message:
          "Delete a semantic layer needs approval from 2 approvers; run it again once it is approved.",
        approval: true,
      },
    },
  },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByRole("link", { name: "View approval requests" })
    ).toHaveAttribute("href", "/administration/permissions/safety");
  },
};

export const DangerZoneReadOnly: Story = { args: { section: "danger-zone", canArchive: false } };

export const Archiving: Story = {
  args: { section: "danger-zone", archive: { ...ARCHIVE, submitting: true } },
};
