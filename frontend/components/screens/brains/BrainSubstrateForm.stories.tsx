import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";
import { expect, fn, userEvent, within } from "storybook/test";

import { CELLS } from "./brain-cells.fixtures";
import { BrainSubstrateForm, type BrainSubstrateFormProps } from "./BrainSubstrateForm";
import { DEFAULT_SUBSTRATE, type SubstrateDraft } from "./substrate-cells";

const meta: Meta<typeof BrainSubstrateForm> = {
  title: "Screens/Brains/BrainSubstrateForm",
  component: BrainSubstrateForm,
  parameters: { layout: "padded" },
  args: { draft: DEFAULT_SUBSTRATE, onChange: fn(), mode: "create", matrix: CELLS },
};
export default meta;

type Story = StoryObj<typeof BrainSubstrateForm>;

const POSTGRES: SubstrateDraft = {
  cell: { authority: "postgres", blobs: "s3", vectors: "in-db", graph: "age" },
  persistence: [],
  replicas: 2,
  image: "",
  port: 8080,
};

function Live(props: Partial<BrainSubstrateFormProps>) {
  const [draft, setDraft] = React.useState(props.draft ?? DEFAULT_SUBSTRATE);
  return (
    <BrainSubstrateForm mode="create" matrix={CELLS} {...props} draft={draft} onChange={setDraft} />
  );
}

/** The files cell: the repository is the canon, nothing to attach. */
export const FilesCell: Story = {
  render: () => <Live />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByText(/Supported by Mimwell/)).toBeInTheDocument();
    for (const p of ["sqlite-volume", "efs", "postgres", "s3"])
      await expect(canvas.getByRole("checkbox", { name: p })).not.toBeChecked();
    // An optional attachment can be added and taken off again.
    await userEvent.click(canvas.getByRole("checkbox", { name: "efs" }));
    await expect(canvas.getByRole("checkbox", { name: "efs" })).toBeChecked();
  },
};

/** Postgres with AGE and S3 blobs: what the cell needs is checked and held. */
export const PostgresCell: Story = {
  render: () => <Live draft={POSTGRES} />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    const postgres = canvas.getByRole("checkbox", { name: "postgres" });
    await expect(postgres).toBeChecked();
    await expect(postgres).toBeDisabled();
    await expect(canvas.getByRole("checkbox", { name: "s3" })).toBeChecked();
    await expect(canvas.getByText("Needed by authority=postgres")).toBeInTheDocument();
    await expect(canvas.getByText(/With the AGE extension/)).toBeInTheDocument();
  },
};

/** SQLite: a volume per replica, each rebuilding its own copy. */
export const SqliteCell: Story = {
  args: {
    draft: {
      ...DEFAULT_SUBSTRATE,
      cell: { ...DEFAULT_SUBSTRATE.cell, authority: "sqlite" },
      replicas: 3,
    },
  },
};

/** Graduation: persistence the layer holds data on stays, and the image is kept. */
export const Graduate: Story = {
  args: {
    mode: "graduate",
    draft: { ...POSTGRES, persistence: ["efs"] },
    locked: ["postgres", "efs"],
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByRole("checkbox", { name: "efs" })).toBeDisabled();
    await expect(canvas.getAllByText(/Holds data/)).toHaveLength(2);
    await expect(canvas.queryByLabelText("Image")).not.toBeInTheDocument();
  },
};

/** The server named persistence: its reason sits there. */
export const ServerRefusedPersistence: Story = {
  args: {
    draft: POSTGRES,
    serverError: {
      field: "persistence",
      message:
        "dropping postgres would leave its data behind; moving down a tier is a knowledge-store cutover in the brain, not a substrate change",
    },
  },
};

/** A refusal about the cell itself leads the form. */
export const ServerRefusedCell: Story = {
  args: {
    serverError: {
      field: "authority",
      message:
        "unsupported cell: authority=files, blobs=db-blob, vectors=local, graph=edges-table: db-blob keeps blobs in the authority database and files has none",
    },
  },
};

/** An unsupported cell, refused with Mimwell's reason as the server words it. */
export const UnsupportedCell: Story = {
  args: { draft: { ...DEFAULT_SUBSTRATE, cell: { ...DEFAULT_SUBSTRATE.cell, graph: "age" } } },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getAllByText(/age is a Postgres extension/)).not.toHaveLength(0);
  },
};

/** The matrix has not arrived: the cell shows without a verdict, nothing is refused here. */
export const MatrixLoading: Story = {
  args: { matrix: null },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.queryByText(/Supported by Mimwell/)).not.toBeInTheDocument();
  },
};

export const BadNumbers: Story = {
  args: {
    draft: { ...DEFAULT_SUBSTRATE, replicas: 40, port: 0, image: "ghcr.io/acme/brain chat" },
  },
};

export const Width768: Story = {
  render: (args) => (
    <div style={{ width: 768 }}>
      <BrainSubstrateForm {...args} draft={POSTGRES} />
    </div>
  ),
};
