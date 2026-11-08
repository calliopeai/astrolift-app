import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import {
  NvkeCredentialRequestPanel,
  type NvkeCredentialRequestPanelProps,
} from "./NvkeCredentialRequestPanel";
import { SITE, OPERATION } from "./fixtures";
import { METRICS_AUTHORITY } from "./NvkeMetricsPanel.stories";
export const COLLECTION_PROPS: NvkeCredentialRequestPanelProps = {
  scopeKey: "owned-fixture-scope",
  site: SITE,
  source: { operationId: OPERATION.id, version: 8 },
  clusterId: "123e4567-e89b-12d3-a456-426614174092",
  authority: METRICS_AUTHORITY,
  loading: false,
  pending: false,
  canManage: true,
  canRecover: false,
  error: null,
  recovery: null,
  onRequest: async () => true,
  onRecover: async () => true,
  onRefresh: () => {},
};
export default {
  title: "Screens/NVKE/Credential collection",
  component: NvkeCredentialRequestPanel,
  excludeStories: ["COLLECTION_PROPS"],
  args: COLLECTION_PROPS,
} satisfies Meta<typeof NvkeCredentialRequestPanel>;
type Story = StoryObj<typeof NvkeCredentialRequestPanel>;
export const Full: Story = {};
export const Loading: Story = { args: { loading: true, canManage: false } };
export const ReadOnly: Story = { args: { canManage: false } };
export const MissingAuthority: Story = { args: { canManage: false, authority: null } };
export const UnknownOutcome: Story = {
  args: {
    canRecover: true,
    recovery: {
      requestId: "123e4567-e89b-12d3-a456-426614174093",
      operationKey: "metrics.credentials",
      timeoutSeconds: 600,
    },
    error: "The collection reply is unconfirmed. Preserve the original request.",
  },
};
export const ReadOnlyRecovery: Story = {
  args: { ...UnknownOutcome.args, canManage: false, authority: null, error: null },
};
export const OtherPendingRequest: Story = {
  args: {
    ...ReadOnlyRecovery.args,
    recovery: {
      requestId: "123e4567-e89b-12d3-a456-426614174093",
      operationKey: "temporal.RenewK8sCertsV2",
      timeoutSeconds: 7200,
    },
  },
};
export const Width768: Story = {
  render: (args) => (
    <div style={{ width: 768 }}>
      <NvkeCredentialRequestPanel {...args} />
    </div>
  ),
};
