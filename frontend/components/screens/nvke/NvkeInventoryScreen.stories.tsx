import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { fakeController } from "@/components/data-table/fixtures";
import { NvkeInventoryScreen } from "./NvkeInventoryScreen";
import { nativeTemplateText } from "@/lib/nvke/contract";
import { nvkeCollectionActions } from "@/lib/nvke/inventory";
import { COLLECTION, INVENTORY_ITEM, OPERATION, SITE_DETAIL } from "./fixtures";
export default {
  title: "Screens/NVKE/Inventory",
  component: NvkeInventoryScreen,
  args: {
    ...SITE_DETAIL,
    actions: nvkeCollectionActions(COLLECTION),
    collection: COLLECTION,
    table: fakeController({ rows: [INVENTORY_ITEM] }),
    operation: {
      ...OPERATION,
      operationKey: COLLECTION.key,
      isInventory: true,
      status: "SUCCEEDED",
    },
    observedAt: "2026-11-07T12:03:00Z",
    templateText: (action) => (action.key === COLLECTION.key ? "{}" : nativeTemplateText(action)),
  },
} satisfies Meta<typeof NvkeInventoryScreen>;
type Story = StoryObj<typeof NvkeInventoryScreen>;
export const Observed: Story = {};
export const BeforeRead: Story = {
  args: { operation: null, observedAt: null, table: fakeController({ state: "empty" }) },
};
export const Loading: Story = { args: { loading: true, collection: null } };
export const Reading: Story = {
  args: {
    operation: { ...OPERATION, isInventory: true, operationKey: COLLECTION.key, status: "RUNNING" },
    observedAt: null,
    table: fakeController({ state: "loading" }),
  },
};
export const Empty: Story = { args: { table: fakeController({ state: "empty" }) } };
export const LoadError: Story = {
  args: {
    error: "NVKE data could not be read.",
    table: fakeController({ state: "error", error: new Error("Fixture failure") }),
  },
};
export const ReadOnly: Story = {
  args: { access: { read: true, manage: false, admin: false, loading: false } },
};
export const LongStrings: Story = {
  args: {
    table: fakeController({
      rows: [
        {
          ...INVENTORY_ITEM,
          name: "very-long-resource-name-".repeat(12),
          resourceVersion: "opaque-version-".repeat(30),
        },
      ],
    }),
  },
};
export const Width768: Story = {
  render: (args) => (
    <div style={{ width: 768 }}>
      <NvkeInventoryScreen {...args} />
    </div>
  ),
};

export const CreateRecovery: Story = {
  args: {
    recovery: {
      operationKey: "createCluster",
      requestId: OPERATION.requestId,
      expectedSiteVersion: 7,
      timeoutSeconds: 7200,
    },
  },
};
