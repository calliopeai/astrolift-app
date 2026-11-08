import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { NvkeOperatorAgentPanel, type NvkeOperatorAgentAuthority } from "./NvkeOperatorAgentPanel";
import { SITE } from "./fixtures";

export const OPERATOR_AGENT_AUTHORITY: NvkeOperatorAgentAuthority = {
  id: "123e4567-e89b-12d3-a456-426614174091",
  version: 2,
  siteId: SITE.id,
  siteVersion: SITE.version,
  endpoint: "https://site-agent.example.test",
  instanceId: "123e4567-e89b-12d3-a456-426614174092",
  bundleManifestSha256: "b".repeat(64),
  isEnabled: true,
  timeoutSeconds: 30,
  hasCredentials: true,
  verifiedAt: "2026-11-08T15:00:00Z",
  verificationCode: "AGENT_VERIFIED",
  deletedAt: null,
};
export default {
  title: "Screens/NVKE/Operator agent",
  component: NvkeOperatorAgentPanel,
  excludeStories: ["OPERATOR_AGENT_AUTHORITY"],
  args: {
    site: SITE,
    authority: OPERATOR_AGENT_AUTHORITY,
    canManage: true,
    pending: false,
    loading: false,
    error: null,
    onRefresh: () => {},
    onConfigure: async () => true,
    onAction: async () => true,
  },
} satisfies Meta<typeof NvkeOperatorAgentPanel>;
type Story = StoryObj<typeof NvkeOperatorAgentPanel>;
export const Full: Story = {};
export const Empty: Story = { args: { authority: null } };
export const Loading: Story = { args: { authority: null, loading: true } };
export const ReadOnly: Story = { args: { canManage: false } };
export const Pending: Story = { args: { pending: true } };
export const Disabled: Story = {
  args: {
    authority: {
      ...OPERATOR_AGENT_AUTHORITY,
      isEnabled: false,
      verifiedAt: null,
      verificationCode: "",
    },
  },
};
export const Unverified: Story = {
  args: { authority: { ...OPERATOR_AGENT_AUTHORITY, verifiedAt: null, verificationCode: "" } },
};
export const Failure: Story = {
  args: {
    error:
      "The configuration outcome is unconfirmed. Refresh current metadata before another change.",
  },
};
export const LongStrings: Story = {
  args: {
    site: { ...SITE, name: "Regional GPU operator site ".repeat(12) },
    authority: {
      ...OPERATOR_AGENT_AUTHORITY,
      endpoint: `https://${"long-site-agent-".repeat(3)}.example.test:9443`,
    },
  },
};
export const Width768: Story = {
  render: (args) => (
    <div style={{ width: 768 }}>
      <NvkeOperatorAgentPanel {...args} />
    </div>
  ),
};
