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
