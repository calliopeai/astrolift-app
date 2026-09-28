import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";

import { CreateIdentityProviderSheet } from "./CreateIdentityProviderSheet";
import { IdentityProvidersScreen } from "./IdentityProvidersScreen";
import {
  CREATE_SHEET,
  LOAD_ERROR,
  LONG_PROVIDERS,
  SCREEN,
} from "./settings-identity-provider.fixtures";

const meta: Meta = {
  title: "Screens/Settings/IdentityProvider/IdentityProvidersScreen",
};
export default meta;

type Story = StoryObj;

const renderCreateSheet = ({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) => <CreateIdentityProviderSheet {...CREATE_SHEET} open={open} onOpenChange={onOpenChange} />;

/** Four providers, Auth0 active with its activation stamp. */
export const Full: Story = {
  render: () => <IdentityProvidersScreen {...SCREEN} renderCreateSheet={renderCreateSheet} />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByText("Auth0 prod")).toBeInTheDocument();
    await expect(canvas.getAllByText("Make active")).toHaveLength(3);
  },
};

export const Loading: Story = {
  render: () => (
    <IdentityProvidersScreen
      {...SCREEN}
      providers={[]}
      loading
      renderCreateSheet={renderCreateSheet}
    />
  ),
};

export const Empty: Story = {
  render: () => (
    <IdentityProvidersScreen {...SCREEN} providers={[]} renderCreateSheet={renderCreateSheet} />
  ),
};

/** The list query failed: the error card shows and the table body stays empty. */
export const LoadFailed: Story = {
  render: () => (
    <IdentityProvidersScreen
      {...SCREEN}
      providers={[]}
      error={LOAD_ERROR}
      renderCreateSheet={renderCreateSheet}
    />
  ),
};

/** Without org.update the row actions are hidden. */
export const ReadOnly: Story = {
  render: () => (
    <IdentityProvidersScreen
      {...SCREEN}
      canManageIdp={false}
      renderCreateSheet={renderCreateSheet}
    />
  ),
};

/** Long names, endpoints, usernames, and an unknown kind that falls back to its raw value. */
export const LongStrings: Story = {
  render: () => (
    <IdentityProvidersScreen
      {...SCREEN}
      providers={LONG_PROVIDERS}
      renderCreateSheet={renderCreateSheet}
    />
  ),
};
