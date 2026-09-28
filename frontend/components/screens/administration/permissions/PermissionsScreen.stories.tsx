import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { useLocalListState } from "@/components/list/use-list-state";

import { assignmentsProps, diagnosticsProps, rolesProps } from "./fixtures";
import { AssignmentsView } from "./AssignmentsView";
import { ASSIGNMENTS_LIST, ROLES_LIST } from "./permissions-lists";
import { PermissionsDiagnosticsView } from "./PermissionsDiagnosticsView";
import { PermissionsScreen } from "./PermissionsScreen";
import { RolesView } from "./RolesView";

const meta: Meta = {
  title: "Screens/Administration/Permissions/PermissionsScreen",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

function Roles() {
  return <RolesView list={useLocalListState(ROLES_LIST)} {...rolesProps()} />;
}
function Assignments() {
  return <AssignmentsView list={useLocalListState(ASSIGNMENTS_LIST)} {...assignmentsProps()} />;
}

/** The gate open: the page passes through with its own header. */
export const RolesPage: Story = {
  render: () => (
    <PermissionsScreen enabled page="roles">
      <Roles />
    </PermissionsScreen>
  ),
};

export const AssignmentsPage: Story = {
  render: () => (
    <PermissionsScreen enabled page="assignments">
      <Assignments />
    </PermissionsScreen>
  ),
};

export const DiagnosticsPage: Story = {
  render: () => (
    <PermissionsScreen enabled page="diagnostics">
      <PermissionsDiagnosticsView {...diagnosticsProps()} />
    </PermissionsScreen>
  ),
};

/** `admin.permissions_enabled` is off: the screen says so and mounts nothing. */
export const TurnedOff: Story = {
  render: () => (
    <PermissionsScreen enabled={false} page="roles">
      <Roles />
    </PermissionsScreen>
  ),
};

export const TurnedOffWidth768: Story = {
  render: () => (
    <div style={{ width: 768 }} className="overflow-hidden border">
      <PermissionsScreen enabled={false} page="assignments">
        <Assignments />
      </PermissionsScreen>
    </div>
  ),
};
