import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";

import { useLocalListState } from "@/components/list/use-list-state";

import {
  AUDITOR_ROLE,
  holdersProps,
  LONG_ROLE,
  OWNER_ROLE,
  RELEASE_ROLE,
  roleDetailProps,
} from "./fixtures";
import { ROLE_HOLDERS_LIST } from "./permissions-lists";
import { RoleDetailScreen } from "./RoleDetailScreen";
import { RoleHoldersTab } from "./RoleHoldersTab";

const meta: Meta = {
  title: "Screens/Administration/Permissions/RoleDetailScreen",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

function Holders() {
  return <RoleHoldersTab list={useLocalListState(ROLE_HOLDERS_LIST)} {...holdersProps()} />;
}

/** A custom role: the matrix edits in place. */
export const CustomPermissions: Story = {
  render: () => <RoleDetailScreen {...roleDetailProps(AUDITOR_ROLE)} tab="permissions" />,
  play: async ({ canvasElement }) => {
    const c = within(canvasElement);
    await expect(c.getByRole("link", { name: "Grant access" })).toBeInTheDocument();
    await expect(c.getByRole("button", { name: "Save permissions" })).toBeDisabled();
  },
};

/** A built-in role: read-only, with Duplicate to customise. */
export const BuiltInPermissions: Story = {
  render: () => <RoleDetailScreen {...roleDetailProps(OWNER_ROLE)} tab="permissions" />,
  play: async ({ canvasElement }) => {
    const c = within(canvasElement);
    await expect(c.getByRole("link", { name: /Duplicate to customise/ })).toHaveAttribute(
      "href",
      "/administration/permissions/roles/new?from=r-owner"
    );
  },
};

export const HoldersTab: Story = {
  render: () => (
    <RoleDetailScreen {...roleDetailProps(RELEASE_ROLE)} tab="holders" holders={<Holders />} />
  ),
};

export const SettingsTab: Story = {
  render: () => <RoleDetailScreen {...roleDetailProps(AUDITOR_ROLE)} tab="settings" />,
};

export const Loading: Story = {
  render: () => (
    <RoleDetailScreen {...roleDetailProps(null, { loading: true })} tab="permissions" />
  ),
};

export const Error: Story = {
  render: () => (
    <RoleDetailScreen
      {...roleDetailProps(null, { error: { message: "upstream timed out" } })}
      tab="permissions"
    />
  ),
};

export const NotFound: Story = {
  render: () => <RoleDetailScreen {...roleDetailProps(null)} tab="permissions" />,
};

/** A viewer without org.manage_members: no Grant access, a matrix that only reads. */
export const ReadOnlyViewer: Story = {
  render: () => (
    <RoleDetailScreen {...roleDetailProps(AUDITOR_ROLE, { canManage: false })} tab="permissions" />
  ),
};

export const LongStrings: Story = {
  render: () => <RoleDetailScreen {...roleDetailProps(LONG_ROLE)} tab="permissions" />,
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }} className="overflow-hidden border">
      <RoleDetailScreen {...roleDetailProps(LONG_ROLE)} tab="permissions" />
    </div>
  ),
};
