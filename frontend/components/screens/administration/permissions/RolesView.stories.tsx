import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";

import { fakeController } from "@/components/data-table/fixtures";
import type { AstroliftRole } from "@/graphql/identity/identity.types";

import { LONG_PERMISSIONS, LONG_ROLE, ROLES, rolesProps } from "./fixtures";
import { RolesView } from "./RolesView";

const meta: Meta = {
  title: "Screens/Administration/Permissions/RolesView",
};
export default meta;

type Story = StoryObj;

const table = (patch: Parameters<typeof fakeController<AstroliftRole>>[0]) =>
  fakeController<AstroliftRole>({ sort: undefined, sortEnabled: false, ...patch });

export const Full: Story = { render: () => <RolesView {...rolesProps()} /> };

export const Loading: Story = {
  render: () => <RolesView {...rolesProps({ table: table({ state: "loading" }) })} />,
};

export const Empty: Story = {
  render: () => <RolesView {...rolesProps({ table: table({ state: "empty" }) })} />,
};

export const NoMatches: Story = {
  render: () => (
    <RolesView
      {...rolesProps({
        table: table({ state: "emptyFiltered", search: "auditor", isFiltered: true }),
      })}
    />
  ),
};

export const Error: Story = {
  render: () => (
    <RolesView
      {...rolesProps({
        table: table({ state: "error", error: new globalThis.Error("upstream timed out") }),
      })}
    />
  ),
};

export const LongStrings: Story = {
  render: () => (
    <RolesView
      {...rolesProps({
        table: table({ rows: [LONG_ROLE, ...ROLES], totalCount: 214, hasNext: true }),
        allPermissions: LONG_PERMISSIONS,
      })}
    />
  ),
};

/** A viewer without org.manage_members: rows open read-only. */
export const ReadOnlyViewer: Story = {
  render: () => <RolesView {...rolesProps({ canManage: false })} />,
};

/** Activating a custom role opens the editor. */
export const EditRole: Story = {
  render: () => <RolesView {...rolesProps()} />,
  play: async ({ canvasElement }) => {
    const c = within(canvasElement);
    await userEvent.click(c.getByRole("button", { name: "Edit role Release Manager" }));
    await expect(await within(document.body).findByText("3 of 16 selected")).toBeInTheDocument();
  },
};
