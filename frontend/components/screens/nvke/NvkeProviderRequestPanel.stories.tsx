import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import {
  NvkeProviderRequestPanel,
  type NvkeProviderRequestPanelProps,
} from "./NvkeProviderRequestPanel";
import { SITE } from "./fixtures";
export const PROVIDER_REQUEST_PROPS: NvkeProviderRequestPanelProps = {
  scopeKey: "provider-collection-fixture",
  site: SITE,
  connection: {
    id: "123e4567-e89b-12d3-a456-426614174091",
    version: 3,
    endpoint: "https://management.example.test:6443",
  },
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
  title: "Screens/NVKE/Provider collection",
  component: NvkeProviderRequestPanel,
  excludeStories: ["PROVIDER_REQUEST_PROPS"],
  args: PROVIDER_REQUEST_PROPS,
} satisfies Meta<typeof NvkeProviderRequestPanel>;
type Story = StoryObj<typeof NvkeProviderRequestPanel>;
export const Full: Story = {};
export const ReadOnly: Story = { args: { canManage: false } };
export const MissingConnection: Story = { args: { canManage: false, connection: null } };
export const Loading: Story = { args: { loading: true } };
export const Failure: Story = { args: { error: "The collection could not be confirmed." } };
export const UnknownOutcome: Story = {
  args: {
    canRecover: true,
    recovery: {
      requestId: "123e4567-e89b-12d3-a456-426614174092",
      operationKey: "infrastructure.providers",
      timeoutSeconds: 600,
    },
  },
};
export const ReadOnlyRecovery: Story = {
  args: { ...UnknownOutcome.args, canManage: false, connection: null },
};
export const OtherPendingRequest: Story = {
  args: {
    ...UnknownOutcome.args,
    canManage: false,
    recovery: { ...UnknownOutcome.args!.recovery!, operationKey: "createCluster" },
  },
};
export const LongStrings: Story = {
  args: {
    site: { ...SITE, name: "long-provider-site-".repeat(20) },
    connection: {
      ...PROVIDER_REQUEST_PROPS.connection!,
      endpoint: "https://management.example.test/" + "long-reviewed-path-".repeat(10),
    },
  },
};
export const Width768: Story = {
  render: (args) => (
    <div style={{ width: 768 }}>
      <NvkeProviderRequestPanel {...args} />
    </div>
  ),
};
