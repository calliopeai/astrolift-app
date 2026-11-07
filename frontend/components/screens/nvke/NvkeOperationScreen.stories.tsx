import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { NvkeOperationScreen } from "./NvkeOperationScreen";
import accessStory from "./NvkeWorkloadAccessPanel.stories";
import { fakeController } from "@/components/data-table/fixtures";
import {
  DIAGNOSTIC_OBSERVATION,
  DIAGNOSTIC_ROW,
  OPERATION,
  OPERATION_DETAIL,
  MAINTENANCE_OBSERVATION,
} from "./fixtures";

export default {
  title: "Screens/NVKE/Operation",
  component: NvkeOperationScreen,
  args: OPERATION_DETAIL,
} satisfies Meta<typeof NvkeOperationScreen>;
type Story = StoryObj<typeof NvkeOperationScreen>;
export const Maintenance: Story = {
  args: {
    operation: { ...OPERATION, operationKey: "getCluster", status: "SUCCEEDED" },
    maintenance: {
      observation: MAINTENANCE_OBSERVATION,
      loading: false,
      error: null,
      onRefresh: () => {},
    },
  },
};
export const ImportedAccess: Story = {
  args: {
    operation: { ...OPERATION, operationKey: "getCluster", status: "SUCCEEDED" },
    workloadAccess: accessStory.args,
  },
};
export const AwaitingReady: Story = {};
export const Loading: Story = { args: { operation: null, loading: true } };
export const LoadError: Story = {
  args: { operation: null, error: "NVKE data could not be read." },
};
export const Uncertain: Story = {
  args: {
    operation: {
      ...OPERATION,
      status: "UNCERTAIN",
      errorCode: "MUTATION_UNCERTAIN",
      stage: "reconciliation_required",
    },
  },
};
export const Succeeded: Story = {
  args: { operation: { ...OPERATION, status: "SUCCEEDED", stage: "ready" } },
};
export const InventorySucceeded: Story = {
  args: {
    operation: {
      ...OPERATION,
      operationKey: "listClusters",
      isInventory: true,
      status: "SUCCEEDED",
      stage: "read_complete",
    },
  },
};
export const Failed: Story = {
  args: {
    operation: {
      ...OPERATION,
      status: "FAILED",
      stage: "request_refused",
      errorCode: "SOURCE_CHANGED",
    },
  },
};
export const ReadOnly: Story = { args: { canAct: false, canResume: false } };
export const LongStrings: Story = {
  args: {
    operation: {
      ...OPERATION,
      operationKey: "longNativeOperationNameWithProviderDetails".repeat(5),
      stage: "waiting_for_provider_specific_readiness_".repeat(6),
      errorCode: "SAFE_PROVIDER_ERROR_CODE_".repeat(4),
    },
  },
};
export const Width768: Story = {
  render: (args) => (
    <div style={{ width: 768 }}>
      <NvkeOperationScreen {...args} />
    </div>
  ),
};

export const Diagnostics: Story = {
  args: {
    operation: { ...OPERATION, operationKey: "getClusterSpireServer", status: "SUCCEEDED" },
    diagnostics: {
      observation: DIAGNOSTIC_OBSERVATION,
      table: fakeController({ rows: [DIAGNOSTIC_ROW] }),
      loading: false,
      error: null,
      onRefresh: () => {},
    },
  },
};
