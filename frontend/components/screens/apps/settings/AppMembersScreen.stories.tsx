import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { fakeController } from "@/components/data-table/fixtures";
import { AppTabsView } from "@/components/screens/apps/detail/AppTabs";
import type { AstroliftRoleBinding } from "@/graphql/identity/identity.types";

import { AppMembersScreen } from "./AppMembersScreen";
import { APP, BINDINGS, LONG, MEMBERS } from "./app-settings-members.fixtures";

const meta: Meta = {
  title: "Screens/Apps/Settings/AppMembersScreen",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

const tabs = <AppTabsView slug="checkout" basePath="/apps" pathname="/apps/checkout/members" />;

export const Full: Story = {
  render: () => <AppMembersScreen {...MEMBERS} tabs={tabs} />,
};

/** The app itself loading. */
export const Loading: Story = {
  render: () => <AppMembersScreen {...MEMBERS} app={null} loading />,
};

/** The bindings page loading under a loaded app. */
export const TableLoading: Story = {
  render: () => (
    <AppMembersScreen
      {...MEMBERS}
      tabs={tabs}
      table={fakeController<AstroliftRoleBinding>({ state: "loading" })}
    />
  ),
};

/** No grants on this app and no APP-scope roles defined. */
export const Empty: Story = {
  render: () => (
    <AppMembersScreen
      {...MEMBERS}
      tabs={tabs}
      appRoles={[]}
      table={fakeController<AstroliftRoleBinding>({ state: "empty" })}
    />
  ),
};

/** The bindings request failed. */
export const TableError: Story = {
  render: () => (
    <AppMembersScreen
      {...MEMBERS}
      tabs={tabs}
      table={fakeController<AstroliftRoleBinding>({
        state: "error",
        error: new Error("Network error: failed to fetch"),
      } as Parameters<typeof fakeController<AstroliftRoleBinding>>[0])}
    />
  ),
};

/** No app with this slug, or no permission to see it. */
export const NotFound: Story = {
  render: () => <AppMembersScreen {...MEMBERS} slug="no-such-app" app={null} />,
};

export const LongStrings: Story = {
  render: () => (
    <AppMembersScreen
      {...MEMBERS}
      app={{ ...APP, slug: LONG }}
      table={fakeController<AstroliftRoleBinding>({
        rows: BINDINGS.map((b) => ({
          ...b,
          user: b.user ? { ...b.user, username: LONG, email: `${LONG}@example.com` } : null,
          groupExternalId: b.user ? "" : LONG,
        })) as AstroliftRoleBinding[],
      })}
    />
  ),
};
