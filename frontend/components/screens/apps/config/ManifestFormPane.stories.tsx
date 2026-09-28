import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";

import { DRAFT, EMPTY_DRAFT, LONG, UNSAFE_DRAFT } from "./app-config-manifest.fixtures";
import { ManifestFormPane } from "./ManifestFormPane";

const meta: Meta<typeof ManifestFormPane> = {
  title: "Screens/Apps/Config/ManifestFormPane",
  component: ManifestFormPane,
  args: { draft: DRAFT, onDraftChange: () => {}, onSwitchToCode: () => {} },
};
export default meta;

type Story = StoryObj<typeof ManifestFormPane>;

/** A valid manifest: builder on the left, live TOML preview on the right. */
export const Full: Story = {};

/** The pane has no loading state (the screen shows the skeleton); an empty draft. */
export const Empty: Story = {
  args: { draft: EMPTY_DRAFT },
};

/** The error state: validation errors listed above the preview. */
export const Invalid: Story = {
  args: { draft: `[[workloads]]\nkind = "cronjob"\n` },
};

/** A manifest the codec can't round-trip: the pane refuses and points at Code. */
export const Unsafe: Story = {
  args: { draft: UNSAFE_DRAFT },
  play: async ({ canvasElement }) => {
    await expect(within(canvasElement).getByRole("button")).toBeInTheDocument();
  },
};

export const LongStrings: Story = {
  args: { draft: `name = "${LONG}"\n\n[[workloads]]\nname = "${LONG}"\nkind = "deployment"\n` },
};
