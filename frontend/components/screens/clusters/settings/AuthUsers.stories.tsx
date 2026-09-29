import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { AuthUsersView } from "./AuthUsers";
import { AUTH_USERS, LONG } from "./fixtures";

const meta: Meta = { title: "Screens/Clusters/Settings/AuthUsers" };
export default meta;

type Story = StoryObj;

const view = AUTH_USERS.view!;

export const Full: Story = { render: () => <AuthUsersView {...AUTH_USERS} /> };

export const Loading: Story = {
  render: () => <AuthUsersView {...AUTH_USERS} view={null} loading />,
};

export const Empty: Story = {
  render: () => <AuthUsersView {...AUTH_USERS} view={{ ...view, users: [] }} />,
};

/** An identity provider the platform cannot manage. */
export const Unsupported: Story = {
  render: () => (
    <AuthUsersView
      {...AUTH_USERS}
      view={{
        ...view,
        supported: false,
        users: [],
        reason: "central auth does not sign in through Amazon Cognito",
      }}
    />
  ),
};

export const LongStrings: Story = {
  render: () => (
    <AuthUsersView
      {...AUTH_USERS}
      view={{
        ...view,
        groups: [LONG],
        users: view.users.map((u) => ({
          ...u,
          email: `${LONG}@example.com`,
          groups: [LONG],
        })),
      }}
    />
  ),
};

const MANY = Array.from({ length: 40 }, (_, i) => ({
  ...view.users[i % view.users.length]!,
  username: `user-${i}`,
  email: `user-${i}@example.com`,
  groups: i % 3 === 0 ? ["platform"] : i % 3 === 1 ? ["finance"] : [],
  enabled: i % 5 !== 0,
}));

/** Forty users: the embedded list pages them; filter by group or state. */
export const ManyUsers: Story = {
  render: () => <AuthUsersView {...AUTH_USERS} view={{ ...view, users: MANY }} />,
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }} className="overflow-hidden border">
      <AuthUsersView {...AUTH_USERS} view={{ ...view, users: MANY }} />
    </div>
  ),
};
