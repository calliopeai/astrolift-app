import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { NvkeTemporalPanel, type NvkeTemporalAuthority } from "./NvkeTemporalPanel";
import { SITE } from "./fixtures";
export const TEMPORAL_AUTHORITY: NvkeTemporalAuthority = {
  id: "123e4567-e89b-12d3-a456-426614174090",
  version: 2,
  siteId: SITE.id,
  siteVersion: SITE.version,
  address: "native-temporal.example.test:7233",
  namespace: "native-controller",
  taskQueue: "native-controller-tasks",
  serverName: "native-temporal.example.test",
  useTls: true,
  isEnabled: true,
  timeoutSeconds: 30,
  hasCredentials: true,
  verifiedAt: "2026-11-07T12:00:00Z",
  verificationCode: "NAMESPACE_VERIFIED",
  deletedAt: null,
};
export default {
  title: "Screens/NVKE/Native Temporal",
  component: NvkeTemporalPanel,
  excludeStories: ["TEMPORAL_AUTHORITY"],
  args: {
    site: SITE,
    authority: TEMPORAL_AUTHORITY,
    canManage: true,
    pending: false,
    loading: false,
    error: null,
    onRefresh: () => {},
    onConfigure: async () => true,
    onAction: async () => true,
  },
} satisfies Meta<typeof NvkeTemporalPanel>;
type Story = StoryObj<typeof NvkeTemporalPanel>;
export const Full: Story = {};
export const Empty: Story = { args: { authority: null } };
export const Loading: Story = { args: { authority: null, loading: true } };
export const ReadOnly: Story = { args: { canManage: false } };
export const Disabled: Story = {
  args: {
    authority: { ...TEMPORAL_AUTHORITY, isEnabled: false, verifiedAt: null, verificationCode: "" },
  },
};
export const Failure: Story = {
  args: {
    error:
      "The configuration outcome is unconfirmed. Refresh current metadata before reviewing another change.",
  },
};
export const LongStrings: Story = {
  args: {
    authority: {
      ...TEMPORAL_AUTHORITY,
      namespace: "long-native-namespace-".repeat(10),
      taskQueue: "long-native-queue-".repeat(10),
    },
  },
};
export const Width768: Story = {
  render: (args) => (
    <div style={{ width: 768 }}>
      <NvkeTemporalPanel {...args} />
    </div>
  ),
};
