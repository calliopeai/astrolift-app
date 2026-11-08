import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { fakeController } from "@/components/data-table/fixtures";
import type { NvkeNativeHistoryPage } from "@/lib/nvke/native-history";
import { NvkeWorkflowPanel, type NvkeWorkflowPanelProps } from "./NvkeWorkflowPanel";
import { TEMPORAL_AUTHORITY } from "./NvkeTemporalPanel.stories";
import { SITE, OPERATION } from "./fixtures";
export const WORKFLOW_HISTORY: NvkeNativeHistoryPage = {
  siteId: SITE.id,
  siteVersion: SITE.version,
  authorityVersion: TEMPORAL_AUTHORITY.version,
  sourceOperationId: OPERATION.id,
  sourceVersion: OPERATION.version,
  sourceObservedAt: "2026-11-07T12:00:00Z",
  clusterId: "123e4567-e89b-12d3-a456-426614174088",
  workflow: "RotateEtcdEncryptionKey",
  workflowId: "rotation-owner-更新",
  runId: "123e4567-e89b-12d3-a456-426614174089",
  status: "FAILED",
  recordedState: "failed",
  recordedPhase: "rewrite",
  retainedMutationLock: true,
  observedAt: "2026-11-07T12:00:01Z",
  items: [
    {
      eventId: "5",
      eventType: "ACTIVITY_TASK_SCHEDULED",
      activityType: "BeginEtcdEncryptionKeyRotation",
      occurredAt: "2026-11-07T10:00:00Z",
      scheduledEventId: null,
      workflowTaskCompletedEventId: "4",
    },
    {
      eventId: "9007199254740993",
      eventType: "ACTIVITY_TASK_SCHEDULED",
      activityType: "StartEtcdEncryptionRewrite",
      occurredAt: "2026-11-07T11:00:00Z",
      scheduledEventId: null,
      workflowTaskCompletedEventId: "9007199254740992",
    },
  ],
  nextCursor: "bound-native-cursor",
};
export const WORKFLOW_PROPS: NvkeWorkflowPanelProps = {
  scopeKey: "actor-41-visit-1",
  site: SITE,
  authority: TEMPORAL_AUTHORITY,
  source: { operationId: OPERATION.id, version: OPERATION.version },
  clusterId: WORKFLOW_HISTORY.clusterId,
  owners: {
    RotateEtcdEncryptionKey: WORKFLOW_HISTORY.workflowId,
    RenewK8sCertsV2: "renewal-owner-更新",
  },
  workflows: (
    ["RenewK8sCertsV2", "RotateEtcdEncryptionKey", "RetireLostEtcdRotationNode"] as const
  ).map((name) => ({
    name,
    schemaText: JSON.stringify(
      {
        type: "object",
        properties: {
          clusterID: { type: "string", format: "uuid" },
          ...(name === "RenewK8sCertsV2"
            ? {
                thresholdDays: { type: "integer" },
                force: { type: "boolean" },
                scheduledFor: { type: "string", format: "date-time" },
              }
            : name === "RetireLostEtcdRotationNode"
              ? {
                  nodeID: { type: "string", format: "uuid" },
                  rotationWorkflowID: { type: "string" },
                }
              : {}),
        },
      },
      null,
      2
    ),
  })),
  canManage: true,
  canRecover: true,
  retirementEligible: true,
  canInspect: true,
  loading: false,
  pending: false,
  error: null,
  recovery: null,
  history: WORKFLOW_HISTORY,
  historyTable: fakeController({
    rows: WORKFLOW_HISTORY.items,
    searchEnabled: false,
    sortEnabled: false,
    hasNext: true,
  }),
  historyError: null,
  onHistory: () => {},
  onRequest: async () => true,
  onRecover: async () => true,
  onRefresh: () => {},
};
export default {
  title: "Screens/NVKE/Native maintenance workflows",
  component: NvkeWorkflowPanel,
  excludeStories: ["WORKFLOW_HISTORY", "WORKFLOW_PROPS"],
  args: WORKFLOW_PROPS,
} satisfies Meta<typeof NvkeWorkflowPanel>;
type Story = StoryObj<typeof NvkeWorkflowPanel>;
export const RetainedRotation: Story = {};
export const Empty: Story = { args: { history: null } };
export const Loading: Story = { args: { history: null, loading: true } };
export const ReadOnly: Story = { args: { canManage: false, canInspect: false, history: null } };
export const MissingAuthority: Story = { args: { authority: null, history: null } };
export const DisabledAuthority: Story = {
  args: { authority: { ...TEMPORAL_AUTHORITY, isEnabled: false }, history: null },
};
export const Failure: Story = {
  args: {
    error: "Refresh current authority and inspect the original request before another admission.",
  },
};
export const Recovering: Story = {
  args: {
    recovery: {
      requestId: OPERATION.requestId,
      operationKey: "temporal.reset.RotateEtcdEncryptionKey",
    },
  },
};
export const Pending: Story = { args: { pending: true } };
export const Completed: Story = { args: { history: { ...WORKFLOW_HISTORY, status: "COMPLETED" } } };
export const StaleHistory: Story = {
  args: {
    historyTable: fakeController({
      rows: WORKFLOW_HISTORY.items,
      searchEnabled: false,
      sortEnabled: false,
      isStale: true,
    }),
  },
};
export const LongStrings: Story = {
  args: {
    site: { ...SITE, name: "Long site name 更新 ".repeat(15) },
    history: { ...WORKFLOW_HISTORY, workflowId: "rotation-owner-更新-".repeat(40) },
    owners: { RotateEtcdEncryptionKey: "rotation-owner-更新-".repeat(40) },
  },
};
export const Width768: Story = {
  render: (args) => (
    <div style={{ width: 768 }}>
      <NvkeWorkflowPanel {...args} />
    </div>
  ),
};

export const ReadOnlyRecovery: Story = {
  args: {
    canManage: false,
    canInspect: false,
    canRecover: true,
    retirementEligible: false,
    history: null,
    recovery: { requestId: OPERATION.requestId, operationKey: "temporal.RenewK8sCertsV2" },
  },
};
export const RetirementNotInspected: Story = { args: { retirementEligible: false, history: null } };
