import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { fakeController } from "@/components/data-table/fixtures";
import { NvkeDiagnosticsPanel } from "./NvkeDiagnosticsPanel";
import { OPERATION, DIAGNOSTIC_ROW as row, DIAGNOSTIC_OBSERVATION } from "./fixtures";
export default {
  title: "Screens/NVKE/Diagnostics",
  component: NvkeDiagnosticsPanel,
  args: {
    observation: DIAGNOSTIC_OBSERVATION,
    table: fakeController({ rows: [row] }),
    loading: false,
    error: null,
    onRefresh: () => {},
  },
} satisfies Meta<typeof NvkeDiagnosticsPanel>;
type Story = StoryObj<typeof NvkeDiagnosticsPanel>;
export const Spire: Story = {};
export const ControlPlane: Story = {
  args: {
    observation: {
      ...DIAGNOSTIC_OBSERVATION,
      summary: {
        ...DIAGNOSTIC_OBSERVATION.summary,
        kind: "control_plane",
        configurationId: OPERATION.id,
        kubernetesVersion: "1.36.0",
        counts: [
          { name: "desiredCount", value: "9007199254740993" },
          { name: "nodeCount", value: "9007199254740993" },
          { name: "readyNodeCount", value: null },
        ],
        flags: [{ name: "recyclingEnabled", value: false }],
        conditions: [{ name: "controlPlaneUpgrading", value: "Unknown" }],
      },
    },
  },
};
export const History: Story = {
  args: {
    observation: {
      ...DIAGNOSTIC_OBSERVATION,
      summary: { ...DIAGNOSTIC_OBSERVATION.summary, kind: "config_history" },
    },
    table: fakeController({
      rows: [
        {
          ...row,
          name: null,
          createdAt: "2026-11-07T12:00:00Z",
          isCurrent: true,
          changedFields: ["kubernetesVersion"],
          changedFieldCount: "2",
          fieldsOmitted: "1",
        },
      ],
    }),
  },
};
export const Loading: Story = { args: { observation: null, loading: true } };
export const LoadError: Story = {
  args: { observation: null, error: "NVKE data could not be read." },
};
export const Empty: Story = { args: { table: fakeController({ state: "empty" }) } };
export const Missing: Story = {
  args: {
    table: fakeController({
      rows: [{ ...row, reachable: null, hasValidSvid: null, svidExpiresAt: null }],
    }),
  },
};
export const LongStrings: Story = {
  args: {
    table: fakeController({
      rows: [
        { ...row, name: "long-node-name-".repeat(20), serverAddress: "long-host-name-".repeat(20) },
      ],
    }),
  },
};
export const Width768: Story = {
  render: (args) => (
    <div style={{ width: 768 }}>
      <NvkeDiagnosticsPanel {...args} />
    </div>
  ),
};
