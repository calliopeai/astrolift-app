import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";

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

export const ReadFailed: Story = {
  render: () => <AuthUsersView {...AUTH_USERS} view={null} error="RAW_PROVIDER_READ_ERROR" />,
};
export const CachedReadFailed: Story = {
  render: () => <AuthUsersView {...AUTH_USERS} error="RAW_PROVIDER_READ_ERROR" />,
};
export const UnknownSource: Story = { render: () => <AuthUsersView {...AUTH_USERS} view={null} /> };
export const FutureMetadata: Story = {
  render: () => (
    <AuthUsersView
      {...AUTH_USERS}
      view={{
        ...view,
        provider: "FUTURE_PROVIDER_LITERAL",
        reachNote: "RAW_FUTURE_REACH_NOTE",
        users: view.users.map((user) => ({ ...user, status: "FUTURE_STATUS_LITERAL" })),
      }}
    />
  ),
};
export const CreateRefused: Story = {
  render: () => <AuthUsersView {...AUTH_USERS} onCreate={async () => false} />,
  play: async ({ canvasElement }) => {
    const body = within(canvasElement.ownerDocument.body);
    await userEvent.click(body.getByRole("button", { name: "Add user" }));
    const dialog = within(await body.findByRole("dialog"));
    await userEvent.type(dialog.getByLabelText("Email"), "reviewed@example.test");
    await userEvent.type(dialog.getByLabelText("Password (optional)"), "TEST_ONLY_TYPED_PASSWORD");
    await userEvent.click(dialog.getByRole("button", { name: "Add user" }));
    await expect(dialog.getByLabelText("Password (optional)")).toHaveValue(
      "TEST_ONLY_TYPED_PASSWORD"
    );
    await expect(body.getByRole("dialog")).toBeVisible();
  },
};
