import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { ALL_PERMISSIONS, LONG_PERMISSIONS, LONG_ROLE, OWNER_ROLE, RELEASE_ROLE } from "./fixtures";
import { RoleEditorSheet, type RoleEditorSheetProps } from "./RoleEditorSheet";

const meta: Meta = {
  title: "Screens/Administration/Permissions/RoleEditorSheet",
};
export default meta;

type Story = StoryObj;

const base: RoleEditorSheetProps = {
  open: true,
  onOpenChange: () => {},
  mode: "edit",
  role: RELEASE_ROLE,
  allPermissions: ALL_PERMISSIONS,
  canManage: true,
  onClone: () => {},
  saving: false,
  onCreate: () => Promise.resolve(true),
  onUpdate: () => Promise.resolve(true),
};

export const EditCustomRole: Story = { render: () => <RoleEditorSheet {...base} /> };

export const CreateBlank: Story = {
  render: () => <RoleEditorSheet {...base} mode="create" role={null} />,
};

/** Create seeded from a system role via "Clone as custom role". */
export const CloneSeed: Story = {
  render: () => <RoleEditorSheet {...base} mode="create" role={OWNER_ROLE} />,
};

/** System roles are read-only; the footer offers a clone. */
export const SystemRoleReadOnly: Story = {
  render: () => <RoleEditorSheet {...base} role={OWNER_ROLE} />,
};

export const NoManageAccess: Story = {
  render: () => <RoleEditorSheet {...base} canManage={false} />,
};

/** The save in flight. */
export const Saving: Story = { render: () => <RoleEditorSheet {...base} saving /> };

/** The flat roles list has not loaded, so the catalogue is empty. */
export const EmptyCatalogue: Story = {
  render: () => <RoleEditorSheet {...base} allPermissions={[]} />,
};

/** A save the server rejects: the handler resolves false and the sheet stays open. */
export const SaveRejected: Story = {
  render: () => <RoleEditorSheet {...base} onUpdate={() => Promise.resolve(false)} />,
};

export const LongStrings: Story = {
  render: () => <RoleEditorSheet {...base} role={LONG_ROLE} allPermissions={LONG_PERMISSIONS} />,
};
