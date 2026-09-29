import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";

import { useLocalListState } from "@/components/list/use-list-state";

import { ACCESS_ROWS_LONG, membersProps } from "./app-access-members.fixtures";
import { APP_ACCESS_LIST } from "./app-access-rows";
import { AppMembersScreen, type AppMembersScreenProps } from "./AppMembersScreen";
import { APP, LONG } from "./app-settings-members.fixtures";

/**
 * People with access, the first section of an app's (or agent's) Access
 * tab: principals with their role and the grant's source, role bindings and
 * team shares in one embedded list.
 */
const meta: Meta = {
  title: "Screens/Apps/Settings/AppMembersScreen",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

function Members({ view, ...overrides }: Partial<AppMembersScreenProps> & { view?: string }) {
  const list = useLocalListState(APP_ACCESS_LIST, view ? { view } : {});
  return <AppMembersScreen {...membersProps(list, overrides)} />;
}

/** Team shares first, then role bindings held by users and an IdP group. */
export const Full: Story = {
  render: () => <Members />,
  play: async ({ canvasElement }) => {
    const c = within(canvasElement);
    await expect(c.getByText("Payments")).toBeInTheDocument();
    await expect(c.getAllByText("okta:platform-oncall").length).toBeGreaterThan(1);
  },
};

/** The app itself loading. */
export const Loading: Story = { render: () => <Members app={null} loading /> };

/** The rows loading under a loaded app. */
export const RowsLoading: Story = { render: () => <Members rows={[]} rowsLoading /> };

/** No grants on this app. */
export const Empty: Story = {
  render: () => <Members rows={[]} totalCount={0} />,
};

/** The Team shares view with none. */
export const EmptyView: Story = {
  render: () => <Members view="teams" rows={[]} totalCount={0} />,
};

export const Error: Story = {
  render: () => <Members rows={[]} error={{ message: "Network error: failed to fetch" }} />,
};

/** No app with this slug, or no permission to see it. */
export const NotFound: Story = { render: () => <Members slug="no-such-app" app={null} /> };

/** More than a page: cursor paging. */
export const Paged: Story = {
  render: () => <Members totalCount={132} nextCursor="c2" />,
};

export const LongStrings: Story = {
  render: () => <Members app={{ ...APP, slug: LONG }} slug={LONG} rows={ACCESS_ROWS_LONG} />,
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }} className="overflow-hidden border p-4">
      <Members app={{ ...APP, slug: LONG }} slug={LONG} rows={ACCESS_ROWS_LONG} />
    </div>
  ),
};
