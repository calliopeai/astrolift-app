import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";
import { expect, fn, userEvent, within } from "storybook/test";

import { CELLS } from "../brain-cells.fixtures";
import type { BrainStatus, BrainStatusResult } from "../brain-status-model";
import {
  GRADUATION_NEEDED,
  HEALTHY,
  NOT_ADOPTED,
  ON_POSTGRES,
  UNKNOWN_VALUES,
  WITH_SQLITE,
} from "../brain-status.fixtures";
import { draftFromSubstrate, heldPersistence, type SubstrateDraft } from "../substrate-cells";
import { BRAIN, ON_POSTGRES as POSTGRES_LAYER } from "./brain-detail.fixtures";
import { BrainSubstrateTab, type BrainSubstrateTabProps } from "./BrainSubstrateTab";

const ready = (status: BrainStatus | null): BrainStatusResult => ({ state: "ready", status });

const GRADUATION: BrainSubstrateTabProps["graduation"] = {
  open: false,
  onOpen: fn(),
  onClose: fn(),
  draft: draftFromSubstrate(HEALTHY.substrate),
  onChange: fn(),
  locked: [],
  matrix: CELLS,
  submitting: false,
  onSubmit: fn(),
  error: null,
};

const meta: Meta<typeof BrainSubstrateTab> = {
  title: "Screens/Brains/Detail/BrainSubstrateTab",
  component: BrainSubstrateTab,
  parameters: { layout: "padded" },
  args: {
    local: BRAIN.substrate,
    status: ready(HEALTHY),
    environment: "production",
    environments: [],
    onRetry: fn(),
    graduateNeeds: null,
    graduation: GRADUATION,
  },
};
export default meta;

type Story = StoryObj<typeof BrainSubstrateTab>;

/** The files cell: the repository is the canon and nothing is attached. */
export const FilesCell: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByText("The repository is the canon")).toBeInTheDocument();
    await expect(canvas.getByText("Nothing attached")).toBeInTheDocument();
  },
};

/** Postgres with AGE and S3 blobs, adopted; the bucket waits for a binding. */
export const PostgresCell: Story = {
  args: { status: ready(ON_POSTGRES) },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByText("Adopted: postgres")).toBeInTheDocument();
    await expect(canvas.getByText("unbound")).toBeInTheDocument();
  },
};

/** Provisioned and still provisioning, not yet taken up by the layer's cutover. */
export const NotAdopted: Story = { args: { status: ready(NOT_ADOPTED) } };

export const SqliteVolumes: Story = { args: { status: ready(WITH_SQLITE) } };

/** A placement and a resource state this build does not know. */
export const UnknownValues: Story = { args: { status: ready(UNKNOWN_VALUES) } };

/** Mimwell asks for graduation: the tab offers it. */
export const GraduationNeeded: Story = {
  args: { status: ready(GRADUATION_NEEDED) },
  play: async ({ args, canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByText(/outgrown its cell/)).toBeInTheDocument();
    await userEvent.click(canvas.getByRole("button", { name: "Graduate" }));
    await expect(args.graduation.onOpen).toHaveBeenCalled();
  },
};

/** The same, for a viewer without app.deploy: the permission it takes, said. */
export const GraduationNeededReadOnly: Story = {
  args: { status: ready(GRADUATION_NEEDED), graduateNeeds: "app.deploy" },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.queryByRole("button", { name: "Graduate" })).not.toBeInTheDocument();
    await expect(canvas.getByText("app.deploy")).toBeInTheDocument();
  },
};

/** The graduation form: the held volume stays attached. */
function Graduating(args: BrainSubstrateTabProps) {
  const [draft, setDraft] = React.useState<SubstrateDraft>(
    draftFromSubstrate(WITH_SQLITE.substrate)
  );
  return (
    <BrainSubstrateTab
      {...args}
      status={ready({
        ...WITH_SQLITE,
        substrate: { ...WITH_SQLITE.substrate, graduationNeeded: true },
      })}
      graduation={{
        ...GRADUATION,
        open: true,
        draft,
        onChange: setDraft,
        locked: heldPersistence(WITH_SQLITE.substrate.persistence),
      }}
    />
  );
}

export const GraduationForm: Story = {
  render: (args) => <Graduating {...args} />,
  play: async ({ canvasElement }) => {
    const body = within(canvasElement.ownerDocument.body);
    const dialog = within(await body.findByRole("dialog"));
    await expect(dialog.getByRole("checkbox", { name: "sqlite-volume" })).toBeDisabled();
    await expect(dialog.getByRole("checkbox", { name: "efs" })).toBeChecked();
    await expect(dialog.getByRole("button", { name: "Graduate" })).toBeEnabled();
  },
};

/** The server refused the graduation; its reason sits on the form. */
export const GraduationRefused: Story = {
  args: {
    status: ready(GRADUATION_NEEDED),
    graduation: {
      ...GRADUATION,
      open: true,
      error: {
        field: "image",
        message: "switching between a built brain and a given image means registering it again",
      },
    },
  },
};

/** While the status read is out, the cell comes from the layer itself. */
export const StatusLoading: Story = {
  args: { local: POSTGRES_LAYER.substrate, status: { state: "loading" } },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByText("Checking the running commit")
    ).toBeInTheDocument();
  },
};

/** The status read failed: the cell last applied still shows, and says so. */
export const StatusFailed: Story = {
  args: {
    local: POSTGRES_LAYER.substrate,
    status: { state: "error", message: "substrate: HTTP 502" },
  },
};

/** Nothing to show yet: no layer substrate and the read in flight. */
export const Loading: Story = { args: { local: null, status: { state: "loading" } } };

export const Width768: Story = {
  args: { status: ready(ON_POSTGRES) },
  render: (args) => (
    <div style={{ width: 768 }}>
      <BrainSubstrateTab {...args} />
    </div>
  ),
};
