import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { NvkeNativePanel } from "./NvkeNativePanel";
import { ACTIONS, SITE_DETAIL } from "./fixtures";

export default {
  title: "Screens/NVKE/Native actions",
  component: NvkeNativePanel,
  args: SITE_DETAIL,
} satisfies Meta<typeof NvkeNativePanel>;
type Story = StoryObj<typeof NvkeNativePanel>;
export const Full: Story = {};
export const Loading: Story = { args: { catalogLoading: true, actions: [] } };
export const ReadOnly: Story = {
  args: { access: { read: true, manage: false, admin: false, loading: false } },
};
export const MissingInterface: Story = {
  args: { site: { ...SITE_DETAIL.site!, connections: [] } },
};
export const ChangedContract: Story = { args: { contractRevision: "different-revision" } };
export const Recovery: Story = {
  args: {
    recovery: {
      requestId: "123e4567-e89b-12d3-a456-426614174007",
      expectedSiteVersion: 4,
      operationKey: ACTIONS[1].key,
      timeoutSeconds: 7200,
    },
  },
};
export const LongStrings: Story = {
  args: {
    actions: [
      {
        ...ACTIONS[0],
        key: "longOperationNameWithProviderDetails".repeat(4),
        path: "/clusters/" + "provider-detail-".repeat(18),
      },
    ],
  },
};
export const Width768: Story = {
  render: (args) => (
    <div style={{ width: 768 }}>
      <NvkeNativePanel {...args} />
    </div>
  ),
};
