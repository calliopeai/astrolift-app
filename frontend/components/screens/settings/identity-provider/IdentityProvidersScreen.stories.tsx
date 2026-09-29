import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";

import { useLocalListState } from "@/components/list/use-list-state";

import { CreateIdentityProviderSheet } from "./CreateIdentityProviderSheet";
import { IDENTITY_PROVIDERS_LIST } from "./identity-providers-list";
import {
  IdentityProvidersScreen as Screen,
  type IdentityProvidersScreenProps,
} from "./IdentityProvidersScreen";
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

/** The screen with its list state in memory, as the hook keeps it. */
function IdentityProvidersScreen(props: IdentityProvidersScreenProps) {
  const list = useLocalListState(IDENTITY_PROVIDERS_LIST);
  return <Screen {...props} list={list} />;
}

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
    // Make active and Delete sit in each row's `⋯`.
    await expect(canvas.getAllByRole("button", { name: /row actions/i })).toHaveLength(4);
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

/** The list query failed: the list shows its error with the message. */
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

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }} className="overflow-hidden border">
      <IdentityProvidersScreen
        {...SCREEN}
        providers={LONG_PROVIDERS}
        renderCreateSheet={renderCreateSheet}
      />
    </div>
  ),
};
