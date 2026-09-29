import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";

import {
  AUDITOR_ROLE,
  CATALOG,
  DETAIL_ROLES,
  LONG_ROLE,
  OWNER_ROLE,
  VIEWER_ROLE,
} from "./fixtures";
import { RolePermissionsTab, type RolePermissionsTabProps } from "./RolePermissionsTab";

const meta: Meta = {
  title: "Screens/Administration/Permissions/RolePermissionsTab",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

const base: RolePermissionsTabProps = {
  role: AUDITOR_ROLE,
  roles: DETAIL_ROLES,
  catalog: CATALOG,
  canManage: true,
  saving: false,
  onSave: () => Promise.resolve(null),
  duplicateHref: "/administration/permissions/roles/new?from=r-auditor",
};

export const Custom: Story = { render: () => <RolePermissionsTab {...base} /> };

/** A cell ticked: the diff against the saved version counts it, and Save turns on. */
export const Edited: Story = {
  render: () => <RolePermissionsTab {...base} />,
  play: async ({ canvasElement }) => {
    const c = within(canvasElement);
    await userEvent.click(c.getAllByRole("checkbox", { name: "app.deploy" })[0]);
    await expect(c.getByRole("button", { name: "Save permissions" })).toBeEnabled();
  },
};

export const BuiltIn: Story = { render: () => <RolePermissionsTab {...base} role={OWNER_ROLE} /> };

/** A built-in, read by someone who cannot manage roles: no Duplicate. */
export const BuiltInReadOnly: Story = {
  render: () => <RolePermissionsTab {...base} role={VIEWER_ROLE} canManage={false} />,
};

export const Saving: Story = { render: () => <RolePermissionsTab {...base} saving /> };

/** The save the server rejects: the reason sits beside the buttons. */
export const SaveRejected: Story = {
  render: () => (
    <RolePermissionsTab
      {...base}
      onSave={() => Promise.resolve("policy.manage is not in the catalog")}
    />
  ),
  play: async ({ canvasElement }) => {
    const c = within(canvasElement);
    await userEvent.click(c.getAllByRole("checkbox", { name: "app.deploy" })[0]);
    await userEvent.click(c.getByRole("button", { name: "Save permissions" }));
    await expect(await c.findByRole("alert")).toHaveTextContent("not in the catalog");
  },
};

/** A role with nothing in it. */
export const Empty: Story = {
  render: () => <RolePermissionsTab {...base} role={{ ...AUDITOR_ROLE, permissions: [] }} />,
};

export const LongStrings: Story = {
  render: () => <RolePermissionsTab {...base} role={{ ...LONG_ROLE, isSystem: false }} />,
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }} className="overflow-hidden border p-4">
      <RolePermissionsTab {...base} />
    </div>
  ),
};
