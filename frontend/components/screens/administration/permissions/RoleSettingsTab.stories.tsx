import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { AUDITOR_ROLE, LONG_ROLE, OWNER_ROLE } from "./fixtures";
import { RoleSettingsTab, type RoleSettingsTabProps } from "./RoleSettingsTab";

const meta: Meta = {
  title: "Screens/Administration/Permissions/RoleSettingsTab",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

const base: RoleSettingsTabProps = {
  role: AUDITOR_ROLE,
  canManage: true,
  saving: false,
  onSave: () => Promise.resolve(null),
};

export const Custom: Story = { render: () => <RoleSettingsTab {...base} /> };

export const BuiltIn: Story = { render: () => <RoleSettingsTab {...base} role={OWNER_ROLE} /> };

/** Without org.manage_members: the fields disabled, the permission named. */
export const ReadOnly: Story = { render: () => <RoleSettingsTab {...base} canManage={false} /> };

export const Saving: Story = { render: () => <RoleSettingsTab {...base} saving /> };

export const LongStrings: Story = {
  render: () => <RoleSettingsTab {...base} role={{ ...LONG_ROLE, isSystem: false }} />,
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }} className="overflow-hidden border p-4">
      <RoleSettingsTab {...base} role={{ ...LONG_ROLE, isSystem: false }} />
    </div>
  ),
};
