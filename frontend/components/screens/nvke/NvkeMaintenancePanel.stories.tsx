import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { NvkeMaintenancePanel } from "./NvkeMaintenancePanel";
import { MAINTENANCE_OBSERVATION } from "./fixtures";
export default {
  title: "Screens/NVKE/Maintenance",
  component: NvkeMaintenancePanel,
  args: { observation: MAINTENANCE_OBSERVATION, loading: false, error: null, onRefresh: () => {} },
} satisfies Meta<typeof NvkeMaintenancePanel>;
type Story = StoryObj<typeof NvkeMaintenancePanel>;
export const RetainedFailure: Story = {};
export const Accepted: Story = {
  args: {
    observation: {
      ...MAINTENANCE_OBSERVATION,
      renewalScheduleOverdueAtObservation: false,
      renewal: {
        ...MAINTENANCE_OBSERVATION.renewal!,
        state: "accepted",
        phase: null,
        completedAt: null,
        failedNodeId: null,
        retainedMutationLock: false,
      },
    },
  },
};
export const NoOp: Story = {
  args: {
    observation: {
      ...MAINTENANCE_OBSERVATION,
      mutationLockReported: false,
      mutationLockOwner: null,
      mutationLockStartedAt: null,
      nextCertificateRenewalDue: "2026-11-08T00:00:00Z",
      renewalScheduleOverdueAtObservation: false,
      renewal: {
        ...MAINTENANCE_OBSERVATION.renewal!,
        state: "succeeded",
        outcome: "unchanged",
        failedNodeId: null,
        phase: null,
        retainedMutationLock: false,
      },
    },
  },
};
export const RetainedRotation: Story = {
  args: {
    observation: {
      ...MAINTENANCE_OBSERVATION,
      mutationLockOwner: "etcd_key_rotation",
      renewal: null,
      etcdKeyRotation: {
        workflowId: "operator-etcd-key-rotation-123e4567-e89b-12d3-a456-426614174088",
        state: "failed",
        phase: "rewrite",
        failureReason: "operation_failed",
        startedAt: "2026-11-07T10:00:00Z",
        lastProgressAt: "2026-11-07T11:00:00Z",
        completedAt: "2026-11-07T11:00:00Z",
        retainedMutationLock: true,
      },
    },
  },
};
export const LongStrings: Story = {
  args: {
    observation: {
      ...MAINTENANCE_OBSERVATION,
      renewal: {
        ...MAINTENANCE_OBSERVATION.renewal!,
        workflowId: "operator-cert-renewal-".repeat(40),
      },
    },
  },
};
export const Missing: Story = {
  args: {
    observation: {
      ...MAINTENANCE_OBSERVATION,
      nextCertificateRenewalDue: null,
      renewalScheduleOverdueAtObservation: null,
      mutationLockReported: null,
      mutationLockOwner: null,
      mutationLockStartedAt: null,
      renewal: null,
      etcdKeyRotation: null,
    },
  },
};
export const Loading: Story = { args: { observation: null, loading: true } };
export const Failure: Story = {
  args: { observation: null, error: "NVKE data could not be read." },
};
export const Width768: Story = {
  render: (args) => (
    <div style={{ width: 768 }}>
      <NvkeMaintenancePanel {...args} />
    </div>
  ),
};
