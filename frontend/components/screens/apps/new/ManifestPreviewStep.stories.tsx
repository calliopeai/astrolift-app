import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";
import { expect, userEvent, within } from "storybook/test";

import {
  AGENT_CONFIG_MANIFEST,
  MANIFEST_STATE,
  MASKED_MANIFEST,
  MONOREPO_MANIFEST_STATE,
  OWN_BUILD_PATHS_MANIFEST,
} from "./apps-wizard-steps-a.fixtures";
import {
  type ManifestFetchState,
  type ManifestPreviewFields,
  ManifestPreviewStepView,
} from "./ManifestPreviewStep";

const meta: Meta = { title: "Screens/Apps/New/ManifestPreviewStep" };
export default meta;

type Story = StoryObj;

const TEMPLATE_MANIFEST = `# astrolift.toml — minimal app manifest
name = "storefront"

[[workloads]]
slug = "web"
kind = "deployment"
replicas = 1

[workloads.containers.app]
image = "ghcr.io/example/storefront"
port = 8080
`;

/** Holds the wizard fields and the editing toggle so inputs work. */
function Demo({
  initial,
  fetchState,
  fetchError = "",
  editing: initialEditing = false,
  maskedEnvValues = false,
}: {
  initial: Partial<ManifestPreviewFields>;
  fetchState: ManifestFetchState;
  fetchError?: string;
  editing?: boolean;
  maskedEnvValues?: boolean;
}) {
  const [state, setState] = React.useState<ManifestPreviewFields>({
    ...MANIFEST_STATE,
    ...initial,
  });
  const [editing, setEditing] = React.useState(initialEditing);
  return (
    <ManifestPreviewStepView
      state={state}
      setState={setState}
      fetchState={fetchState}
      fetchError={fetchError}
      editing={editing}
      setEditing={setEditing}
      refetch={async () => {}}
      maskedEnvValues={maskedEnvValues}
    />
  );
}

/** Fetching the manifest from the picked repo. */
export const Loading: Story = {
  render: () => (
    <Demo
      fetchState="fetching"
      initial={{ manifestRaw: "", manifestFromRepo: false, manifestValid: false }}
    />
  ),
};

/** No manifest at that path: a template is seeded and the editor opens. */
export const Empty: Story = {
  render: () => (
    <Demo
      fetchState="missing"
      editing
      initial={{ manifestRaw: TEMPLATE_MANIFEST, manifestFromRepo: false }}
    />
  ),
};

export const FetchFailed: Story = {
  render: () => (
    <Demo
      fetchState="error"
      fetchError="repository not found or token lacks contents:read"
      editing
      initial={{
        manifestRaw: "",
        manifestFromRepo: false,
        manifestValid: false,
        manifestErrors: ["Manifest is empty."],
      }}
    />
  ),
};

/** Loaded from the repo, read-only until Edit, structurally valid. */
export const Full: Story = { render: () => <Demo fetchState="found" initial={{}} /> };

/** Pasted text missing the required pieces. */
export const Invalid: Story = {
  render: () => (
    <Demo
      fetchState="missing"
      editing
      initial={{
        manifestRaw: "[service]\nport = 8080\n",
        manifestFromRepo: false,
        manifestValid: false,
        manifestErrors: [
          'Top-level `name = "..."` is required.',
          "At least one `[[workloads]]` block is required.",
        ],
      }}
    />
  ),
};

export const AgentConfigRepo: Story = {
  render: () => (
    <Demo
      fetchState="found"
      initial={{
        manifestRaw: AGENT_CONFIG_MANIFEST,
        manifestValid: false,
        manifestErrors: [
          'Top-level `name = "..."` is required.',
          "At least one `[[workloads]]` block is required.",
        ],
      }}
    />
  ),
};

export const MaskedEnvValues: Story = {
  render: () => (
    <Demo fetchState="found" maskedEnvValues initial={{ manifestRaw: MASKED_MANIFEST }} />
  ),
};

/** A manifest in a sub-directory: the app builds from that directory. */
export const MonorepoBuildContext: Story = {
  render: () => <Demo fetchState="found" initial={MONOREPO_MANIFEST_STATE} />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByLabelText("Build context")).toHaveValue("src/abbott-cgm");
    await expect(canvas.getByLabelText("Dockerfile")).toHaveValue("Dockerfile");
  },
};

/** The manifest sets its own build paths, so the app keeps the repository root. */
export const ManifestSetsBuildPaths: Story = {
  render: () => (
    <Demo
      fetchState="found"
      initial={{ ...MONOREPO_MANIFEST_STATE, manifestRaw: OWN_BUILD_PATHS_MANIFEST }}
    />
  ),
};

/** Typed over: the value is the operator's; clearing it brings back the detected one. */
export const BuildPathsEdited: Story = {
  render: () => <Demo fetchState="found" initial={MONOREPO_MANIFEST_STATE} />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    const context = canvas.getByLabelText("Build context");
    await userEvent.clear(context);
    await userEvent.type(context, ".");
    await expect(context).toHaveValue(".");
    await expect(canvas.getByText(/Clear it to go back/)).toBeInTheDocument();
  },
};

export const ManifestLater: Story = {
  render: () => <Demo fetchState="found" initial={{ manifestLater: true }} />,
};

export const LongStrings: Story = {
  render: () => (
    <Demo
      fetchState="error"
      fetchError="upstream SCM returned 502 Bad Gateway while reading services/payments-reconciliation-worker/deploy/astrolift.toml at ref release/2026-09-quarterly-hardening"
      editing
      initial={{
        manifestPath: "services/payments-reconciliation-worker/deploy/astrolift.toml",
        defaultBranch: "release/2026-09-quarterly-hardening-and-dependency-refresh",
      }}
    />
  ),
};
