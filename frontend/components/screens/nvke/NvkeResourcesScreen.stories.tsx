import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { fakeController } from "@/components/data-table/fixtures";
import { NvkeResourcesScreen } from "./NvkeResourcesScreen";
import { COLLECTION, SITE } from "./fixtures";
export default {
  title: "Screens/NVKE/Collections",
  component: NvkeResourcesScreen,
  args: {
    site: SITE,
    collections: fakeController({ rows: [COLLECTION], searchEnabled: false }),
    loading: false,
    error: null,
    onRefresh: () => {},
  },
} satisfies Meta<typeof NvkeResourcesScreen>;
type Story = StoryObj<typeof NvkeResourcesScreen>;
export const Full: Story = {};
export const Loading: Story = { args: { site: null, loading: true } };
export const LoadError: Story = { args: { site: null, error: "NVKE data could not be read." } };
export const Empty: Story = {
  args: { collections: fakeController({ state: "empty", searchEnabled: false }) },
};
export const LongStrings: Story = {
  args: {
    collections: fakeController({
      rows: [
        {
          ...COLLECTION,
          key: "veryLongNativeCollectionName".repeat(8),
          resourceKind: "VeryLongNativeResourceKind".repeat(8),
        },
      ],
      searchEnabled: false,
    }),
  },
};
export const Width768: Story = {
  render: (args) => (
    <div style={{ width: 768 }}>
      <NvkeResourcesScreen {...args} />
    </div>
  ),
};
