import { NextIntlClientProvider } from "next-intl";
import de from "@/messages/de.json";
import ja from "@/messages/ja.json";
import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { GrantRoleSheet } from "./GrantRoleDialog";
import { LONG_ROLE, ROLES, grantRoleProps } from "./members.fixtures";

const meta: Meta = {
  title: "Screens/Members/GrantRoleDialog",
};
export default meta;

type Story = StoryObj;

/** Open from the page header: the user PK is typed by hand. */
export const Full: Story = { render: () => <GrantRoleSheet {...grantRoleProps()} /> };

/** Opened from a People row: the user PK is prefilled and locked. */
export const DeepLink: Story = {
  render: () => (
    <GrantRoleSheet {...grantRoleProps({ initialUserId: "2", initialUserLabel: "grace" })} />
  ),
};

/** Organization and target lists are still loading. */
export const Loading: Story = {
  render: () => (
    <GrantRoleSheet
      {...grantRoleProps({
        org: null,
        teams: undefined,
        projects: undefined,
        organizationState: { loading: true, error: undefined },
      })}
    />
  ),
};

/** No roles to pick and no scopes to bind to. */
export const Empty: Story = {
  render: () => (
    <GrantRoleSheet {...grantRoleProps({ roles: [], org: null, teams: [], projects: [] })} />
  ),
};

/**
 * The grant in flight. A rejected grant has no in-sheet error (the hook
 * toasts it and the sheet stays open), so this is the closest state.
 */
export const Granting: Story = {
  render: () => (
    <GrantRoleSheet
      {...grantRoleProps({
        initialUserId: "2",
        initialUserLabel: "grace",
        granting: true,
        onGrant: () => Promise.resolve(false),
      })}
    />
  ),
};

export const LongStrings: Story = {
  render: () => (
    <GrantRoleSheet
      {...grantRoleProps({
        roles: [LONG_ROLE, ...ROLES],
        initialUserId: "5",
        initialUserLabel: "maximiliana.vandersloot-oyelaran",
      })}
    />
  ),
};

export const GermanWidth768: Story = {
  decorators: [
    (Story) => (
      <NextIntlClientProvider locale="de" messages={de} timeZone="Europe/Berlin">
        <Story />
      </NextIntlClientProvider>
    ),
  ],
  render: () => (
    <div style={{ width: 768 }}>
      <GrantRoleSheet
        {...grantRoleProps({ initialUserId: "2", initialUserLabel: "RAW_USER_NAME" })}
      />
    </div>
  ),
};
export const JapaneseNoOrg: Story = {
  decorators: [
    (Story) => (
      <NextIntlClientProvider locale="ja" messages={ja} timeZone="Asia/Tokyo">
        <Story />
      </NextIntlClientProvider>
    ),
  ],
  render: () => <GrantRoleSheet {...grantRoleProps({ org: null, teams: [], projects: [] })} />,
};

export const RoleReadFailed: Story = {
  render: () => (
    <GrantRoleSheet
      {...grantRoleProps({
        rolesError: { message: "RAW_ROLE_READ_FAILURE" },
        rolesKnown: false,
        onRetryRoles: async () => {},
      })}
    />
  ),
};
export const RolesUnknown: Story = {
  render: () => <GrantRoleSheet {...grantRoleProps({ roles: [], rolesKnown: false })} />,
};
