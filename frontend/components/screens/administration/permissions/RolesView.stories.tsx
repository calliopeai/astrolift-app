import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";

import { type ListState, useLocalListState } from "@/components/list/use-list-state";

import { pageData } from "../access/fixtures";
import { LONG_ROLE, ROLES, rolesProps } from "./fixtures";
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

/** A viewer without org.manage_members: no New role; rows still open the role read-only. */
export const ReadOnlyViewer: Story = {
  render: () => <Roles {...rolesProps()} />,
  globals: { permissions: "none" },
};

/** Each row is a link to the role's page. */
export const RowsLinkToTheRole: Story = {
  render: () => <Roles {...rolesProps()} />,
  play: async ({ canvasElement }) => {
    const link = within(canvasElement).getByRole("link", { name: /Release Manager/ });
    await expect(link).toHaveAttribute("href", "/administration/permissions/roles/r-release");
  },
};
