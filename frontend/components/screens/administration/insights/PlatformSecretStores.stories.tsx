import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { PLATFORM_SECRETS } from "./platform-settings-fixtures";
import { PlatformSecretStores } from "./PlatformSecretStores";

const meta: Meta = {
  title: "Screens/Administration/Insights/Platform secret stores",
};
export default meta;

type Story = StoryObj;

export const Checked: Story = { render: () => <PlatformSecretStores {...PLATFORM_SECRETS} /> };

export const NotChecked: Story = {
  render: () => <PlatformSecretStores {...PLATFORM_SECRETS} inventory={null} />,
};

export const Loading: Story = {
  render: () => (
    <PlatformSecretStores {...PLATFORM_SECRETS} stores={[]} storesLoading inventory={null} />
  ),
};

export const InventoryError: Story = {
  render: () => (
    <PlatformSecretStores
      {...PLATFORM_SECRETS}
      inventory={null}
      inventoryError={new Error("only the platform operator may do this")}
    />
  ),
};
