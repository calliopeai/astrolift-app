import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { NvkeMetricsPanel, type NvkeMetricsAuthority } from "./NvkeMetricsPanel";
import { SITE } from "./fixtures";

export const METRICS_AUTHORITY: NvkeMetricsAuthority = {
  id: "123e4567-e89b-12d3-a456-426614174091",
  version: 2,
  siteId: SITE.id,
  siteVersion: SITE.version,
  endpoint: "https://metrics.example.test/controller/metrics",
  allowHttp: false,
  isEnabled: true,
  timeoutSeconds: 30,
  hasCredentials: true,
  verifiedAt: "2026-11-08T12:00:00Z",
  verificationCode: "METRICS_READ",
  deletedAt: null,
};
export default {
  title: "Screens/NVKE/Metrics authority",
  component: NvkeMetricsPanel,
  excludeStories: ["METRICS_AUTHORITY"],
  args: {
    site: SITE,
    authority: METRICS_AUTHORITY,
    canManage: true,
    pending: false,
    loading: false,
    error: null,
    onRefresh: () => {},
    onConfigure: async () => true,
    onAction: async () => true,
  },
} satisfies Meta<typeof NvkeMetricsPanel>;
type Story = StoryObj<typeof NvkeMetricsPanel>;
export const Full: Story = {};
export const Empty: Story = { args: { authority: null } };
export const Loading: Story = { args: { authority: null, loading: true } };
export const ReadOnly: Story = { args: { canManage: false } };
export const Disabled: Story = {
  args: {
    authority: { ...METRICS_AUTHORITY, isEnabled: false, verifiedAt: null, verificationCode: "" },
  },
};
export const Failure: Story = {
  args: { error: "Refresh current metadata before reviewing another metrics change." },
};
export const LongStrings: Story = {
  args: {
    authority: {
      ...METRICS_AUTHORITY,
      endpoint: "https://metrics.example.test/" + "controller/".repeat(120),
    },
  },
};
export const Width768: Story = {
  render: (args) => (
    <div style={{ width: 768 }}>
      <NvkeMetricsPanel {...args} />
    </div>
  ),
};
