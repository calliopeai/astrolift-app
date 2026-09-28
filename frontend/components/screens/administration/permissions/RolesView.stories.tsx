import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";

import { type ListState, useLocalListState } from "@/components/list/use-list-state";

import { pageData } from "../access/fixtures";
import { LONG_PERMISSIONS, LONG_ROLE, ROLES, rolesProps } from "./fixtures";
import { ROLES_LIST } from "./permissions-lists";
import { RolesView, type RolesViewProps } from "./RolesView";

const meta: Meta = {
  title: "Screens/Administration/Permissions/RolesView",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

function Roles({
  initial,
  ...props
}: Omit<RolesViewProps, "list"> & { initial?: Partial<ListState> }) {
  const list = useLocalListState(ROLES_LIST, initial);
  return <RolesView list={list} {...props} />;
}

export const Full: Story = { render: () => <Roles {...rolesProps()} /> };

export const Loading: Story = {
  render: () => <Roles {...rolesProps({ page: pageData([], { loading: true }) })} />,
};

export const Empty: Story = {
  render: () => <Roles {...rolesProps({ page: pageData([]) })} />,
};

export const NoMatches: Story = {
  render: () => <Roles {...rolesProps({ page: pageData([]) })} initial={{ q: "auditor" }} />,
};

export const Error: Story = {
  render: () => (
    <Roles {...rolesProps({ page: pageData([], { error: { message: "upstream timed out" } }) })} />
  ),
};

export const LongStrings: Story = {
  render: () => (
    <Roles
      {...rolesProps({
        page: pageData([LONG_ROLE, ...ROLES], { totalCount: 214, nextCursor: "c2" }),
        allPermissions: LONG_PERMISSIONS,
      })}
    />
  ),
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }} className="overflow-hidden border">
      <Roles {...rolesProps({ page: pageData([LONG_ROLE, ...ROLES]) })} />
    </div>
  ),
};

/** A viewer without org.manage_members: rows open read-only. */
export const ReadOnlyViewer: Story = {
  render: () => <Roles {...rolesProps({ canManage: false })} />,
};

/** Activating a custom role opens the editor. */
export const EditRole: Story = {
  render: () => <Roles {...rolesProps()} />,
  play: async ({ canvasElement }) => {
    const c = within(canvasElement);
    await userEvent.click(c.getByRole("button", { name: "Edit role Release Manager" }));
    await expect(await within(document.body).findByText("3 of 16 selected")).toBeInTheDocument();
  },
};
