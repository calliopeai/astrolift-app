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

/**
 * The sheet has no loading state of its own; the closest is the org and
 * scope lists not having arrived yet, so the scope picker is disabled.
 */
export const Loading: Story = {
  render: () => (
    <GrantRoleSheet {...grantRoleProps({ org: null, teams: undefined, projects: undefined })} />
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
