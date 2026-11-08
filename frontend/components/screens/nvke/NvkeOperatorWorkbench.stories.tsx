import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { NvkeOperatorWorkbench } from "./NvkeOperatorWorkbench";
import { OPERATOR_AGENT_AUTHORITY } from "./NvkeOperatorAgentPanel.stories";
import { SITE } from "./fixtures";
import contract from "./operator-contract.fixture.json";
import { operatorContractSchema } from "@/lib/nvke/operator-contract";

export default {
  title: "Screens/NVKE/Operator workbench",
  component: NvkeOperatorWorkbench,
  args: {
    scopeKey: "storybook-operator-workbench",
    site: SITE,
    authority: OPERATOR_AGENT_AUTHORITY,
    contract: operatorContractSchema.parse(contract),
    loading: false,
    pending: false,
    error: null,
    canManage: true,
    canRecover: false,
    recovery: null,
    onRequest: async () => true,
    onRecover: async () => false,
    onRefresh: () => {},
  },
} satisfies Meta<typeof NvkeOperatorWorkbench>;
type Story = StoryObj<typeof NvkeOperatorWorkbench>;
export const Full: Story = {};
export const Empty: Story = { args: { authority: null, canManage: false } };
export const Loading: Story = { args: { loading: true, contract: null } };
export const ReadOnly: Story = { args: { canManage: false } };
export const Pending: Story = { args: { pending: true } };
export const Failure: Story = {
  args: { error: "Refresh current metadata before another request." },
};
export const Recovery: Story = {
  args: {
    canRecover: true,
    recovery: {
      requestId: "123e4567-e89b-12d3-a456-426614174093",
      operationKey: "tooling.generate.sitespec",
      timeoutSeconds: 600,
      acknowledgeInfrastructureChange: false,
      expectedSiteVersion: SITE.version,
      authorityVersion: OPERATOR_AGENT_AUTHORITY.version,
    },
  },
};
export const RecoveryAfterAuthorityRemoval: Story = {
  args: { ...Recovery.args, authority: null, canManage: false },
};
export const LongStrings: Story = {
  args: { site: { ...SITE, name: "Regional GPU operator site ".repeat(12) } },
};
export const Width768: Story = {
  render: (args) => (
    <div style={{ width: 768 }}>
      <NvkeOperatorWorkbench {...args} />
    </div>
  ),
};
