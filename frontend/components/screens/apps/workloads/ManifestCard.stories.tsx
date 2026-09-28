import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { LONG, MANIFEST, MANIFEST_EMPTY, MANIFEST_ERROR } from "./app-workloads.fixtures";
import { ManifestCardView } from "./ManifestCard";

const meta: Meta<typeof ManifestCardView> = {
  title: "Screens/Apps/Workloads/ManifestCard",
  component: ManifestCardView,
  args: MANIFEST,
};
export default meta;

type Story = StoryObj<typeof ManifestCardView>;

export const Full: Story = {};

/** Side-by-side diff against the previously deployed image tag. */
export const Diff: Story = { args: { defaultMode: "diff" } };

export const Loading: Story = { args: { manifest: null, loading: true } };

/** The workload renders no resources; Diff is disabled with no prior deploy. */
export const Empty: Story = { args: MANIFEST_EMPTY };

/** The manifest failed to render, with the path and position of the fault. */
export const ManifestFailed: Story = { args: MANIFEST_ERROR };

export const LongStrings: Story = {
  args: {
    manifest: {
      ...MANIFEST.manifest!,
      environmentName: LONG,
      namespace: LONG,
      imageTag: LONG,
      resources: [
        {
          apiVersion: "v1",
          kind: "ConfigMap",
          metadata: { name: LONG },
          data: { [LONG]: `${LONG} ${LONG} ${LONG}` },
        },
      ],
    },
  },
};

/** The narrowest the web console goes (spec 44 §6): long lines scroll inside the panel. */
export const Width768: Story = {
  args: MANIFEST,
  decorators: [
    (Story) => (
      <div style={{ width: 768 }} className="overflow-hidden">
        <Story />
      </div>
    ),
  ],
};
