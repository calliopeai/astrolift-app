import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { fakeController } from "@/components/data-table/fixtures";
import { NvkeWorkloadAccessPanel, type NvkeAccessBinding } from "./NvkeWorkloadAccessPanel";
import { SITE, OPERATION } from "./fixtures";
export const ACCESS_BINDING: NvkeAccessBinding = {
  id: "123e4567-e89b-12d3-a456-426614174099",
  version: 1,
  siteId: SITE.id,
  clusterId: "123e4567-e89b-12d3-a456-426614174088",
  controllerConnectionId: SITE.connections[0].id,
  sourceOperationId: OPERATION.id,
  upstreamRevision: SITE.upstreamRevision,
  createdAt: "2026-11-07T12:00:00Z",
  deletedAt: null,
};
export default {
  title: "Screens/NVKE/Workload access",
  component: NvkeWorkloadAccessPanel,
  excludeStories: ["ACCESS_BINDING"],
  args: {
    site: SITE,
    table: fakeController({ rows: [ACCESS_BINDING] }),
    candidate: {
      clusterId: ACCESS_BINDING.clusterId,
      sourceOperationId: OPERATION.id,
      sourceVersion: 9,
      siteVersion: SITE.version,
    },
    candidateLoading: false,
    candidateFailed: false,
    canManage: true,
    pending: false,
    error: null,
    enrolled: false,
    onRefresh: () => {},
    onEnroll: async () => true,
    onRevoke: async () => true,
  },
} satisfies Meta<typeof NvkeWorkloadAccessPanel>;
type Story = StoryObj<typeof NvkeWorkloadAccessPanel>;
export const Full: Story = {};
export const ReadOnly: Story = { args: { canManage: false, candidate: null } };
export const Empty: Story = {
  args: { candidate: null, table: fakeController({ state: "empty" }) },
};
export const Pending: Story = { args: { pending: true } };
export const Failure: Story = {
  args: { error: "The grant outcome could not be confirmed. Refresh the list before retrying." },
};
export const SourceUnavailable: Story = { args: { candidate: null, candidateFailed: true } };
export const Width768: Story = {
  render: (args) => (
    <div style={{ width: 768 }}>
      <NvkeWorkloadAccessPanel {...args} />
    </div>
  ),
};
