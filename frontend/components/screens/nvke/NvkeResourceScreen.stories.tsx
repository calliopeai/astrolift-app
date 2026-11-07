import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { NvkeResourceScreen } from "./NvkeResourceScreen";
import { COLLECTION, INVENTORY_ITEM, OPERATION, SITE, SITE_DETAIL } from "./fixtures";
export default {
  title: "Screens/NVKE/Resource",
  component: NvkeResourceScreen,
  args: {
    siteId: SITE.id,
    siteName: SITE.name,
    operationId: OPERATION.id,
    collection: COLLECTION,
    item: INVENTORY_ITEM,
    observedAt: "2026-11-07T12:03:00Z",
    loading: false,
    pending: false,
    error: null,
    canRead: true,
    onRefresh: () => {},
    onDownload: async () => {},
  },
} satisfies Meta<typeof NvkeResourceScreen>;
type Story = StoryObj<typeof NvkeResourceScreen>;
export const Full: Story = {};
export const Loading: Story = { args: { item: null, loading: true } };
export const LoadError: Story = { args: { item: null, error: "NVKE data could not be read." } };
export const ReadOnly: Story = { args: { canRead: false } };
export const Pending: Story = { args: { pending: true } };
export const LongStrings: Story = {
  args: {
    item: {
      ...INVENTORY_ITEM,
      name: "resource-name-".repeat(30),
      resourceVersion: "opaque-version-".repeat(40),
      conditionsOmitted: 100,
    },
  },
};
export const Width768: Story = {
  render: (args) => (
    <div style={{ width: 768 }}>
      <NvkeResourceScreen {...args} />
    </div>
  ),
};

export const WithEditor: Story = {
  args: {
    resourceActions: {
      siteName: SITE.name,
      siteVersion: SITE.version,
      target: INVENTORY_ITEM.name,
      actions: COLLECTION.actions.filter((a) => a.mutates && !["POST", "create"].includes(a.verb)),
      selectedKey: "updateCluster",
      access: SITE_DETAIL.access,
      sourceReady: true,
      pending: false,
      error: null,
      recovery: null,
      editor: {
        id: "reviewed-resource",
        siteVersion: SITE.version,
        operationKey: "updateCluster",
        parametersText: '{"body":{"controlPlaneConfig":{"desiredCount":3}}}',
        preparationActionKey: null,
        concurrency: "observed_only",
        source: {
          operationId: OPERATION.id,
          version: OPERATION.version,
          resourceKey: INVENTORY_ITEM.key,
        },
      },
      schemaText: "{}",
      onSelect: () => {},
      onLoad: async () => true,
      onRefresh: () => {},
      onPrepare: async () => true,
      onRecover: async () => true,
      onRequest: async () => true,
    },
  },
};
