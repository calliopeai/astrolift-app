import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";

import { type ListState, useLocalListState } from "@/components/list/use-list-state";

import { pageData } from "../access/fixtures";
import { holdersProps, LONG_BINDINGS, RELEASE_HOLDERS } from "./fixtures";
import { ROLE_HOLDERS_LIST } from "./permissions-lists";
import { RoleHoldersTab, type RoleHoldersTabProps } from "./RoleHoldersTab";

const meta: Meta = {
  title: "Screens/Administration/Permissions/RoleHoldersTab",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

function Holders({
  initial,
  ...props
}: Omit<RoleHoldersTabProps, "list"> & { initial?: Partial<ListState> }) {
  const list = useLocalListState(ROLE_HOLDERS_LIST, initial);
  return <RoleHoldersTab list={list} {...props} />;
}

/** Two people and an IdP group's mapping, which links to the group. */
export const Full: Story = {
  render: () => (
    <Holders {...holdersProps({ page: pageData(RELEASE_HOLDERS, { totalCount: 31 }) })} />
  ),
  play: async ({ canvasElement }) => {
    const c = within(canvasElement);
    await expect(c.getAllByRole("link", { name: /okta:release-captains/ })[0]).toHaveAttribute(
      "href",
      "/administration/access/people/group%3Aokta%3Arelease-captains"
    );
  },
};

/** Only the IdP groups that hold it: a `kind:group` chip. */
export const GroupsOnly: Story = {
  render: () => (
    <Holders
      {...holdersProps({ page: pageData(RELEASE_HOLDERS.filter((b) => !b.user)) })}
      initial={{ filters: { kind: "group" } }}
    />
  ),
};

export const Loading: Story = {
  render: () => <Holders {...holdersProps({ page: pageData([], { loading: true }) })} />,
};

export const Empty: Story = {
  render: () => <Holders {...holdersProps({ page: pageData([]) })} />,
};

export const NoMatches: Story = {
  render: () => <Holders {...holdersProps({ page: pageData([]) })} initial={{ q: "nobody" }} />,
};

export const Error: Story = {
  render: () => (
    <Holders
      {...holdersProps({ page: pageData([], { error: { message: "upstream timed out" } }) })}
    />
  ),
};

export const ReadOnly: Story = {
  render: () => <Holders {...holdersProps({ canManage: false })} />,
};

export const LongStrings: Story = {
  render: () => (
    <Holders {...holdersProps({ page: pageData([...LONG_BINDINGS, ...RELEASE_HOLDERS]) })} />
  ),
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }} className="overflow-hidden border p-4">
      <Holders {...holdersProps({ page: pageData([...LONG_BINDINGS, ...RELEASE_HOLDERS]) })} />
    </div>
  ),
};
