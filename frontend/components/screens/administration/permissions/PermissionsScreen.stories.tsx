import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { fakeController } from "@/components/data-table/fixtures";
import type { AstroliftRole, AstroliftRoleBinding } from "@/graphql/identity/identity.types";

import { AssignmentsView } from "./AssignmentsView";
import {
  LONG_BINDINGS,
  LONG_PERMISSIONS,
  LONG_ROLE,
  ROLES,
  assignmentsProps,
  diagnosticsProps,
  rolesProps,
} from "./fixtures";
import { PermissionsDiagnosticsView } from "./PermissionsDiagnosticsView";
import { PermissionsScreen } from "./PermissionsScreen";
import { RolesView } from "./RolesView";

const meta: Meta = {
  title: "Screens/Administration/Permissions/PermissionsScreen",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

const rolesTable = (patch: Parameters<typeof fakeController<AstroliftRole>>[0]) =>
  fakeController<AstroliftRole>({ sort: undefined, sortEnabled: false, ...patch });
const bindingsTable = (patch: Parameters<typeof fakeController<AstroliftRoleBinding>>[0]) =>
  fakeController<AstroliftRoleBinding>({ sort: undefined, sortEnabled: false, ...patch });

const full = {
  roles: <RolesView {...rolesProps()} />,
  assignments: <AssignmentsView {...assignmentsProps()} />,
  diagnostics: <PermissionsDiagnosticsView {...diagnosticsProps()} />,
};

export const Full: Story = { render: () => <PermissionsScreen enabled {...full} /> };

export const AssignmentsTab: Story = {
  render: () => <PermissionsScreen enabled initialTab="assignments" {...full} />,
};

export const DiagnosticsTab: Story = {
  render: () => <PermissionsScreen enabled initialTab="diagnostics" {...full} />,
};

/** `admin.permissions_enabled` is off: the screen says so and mounts no tab. */
export const TurnedOff: Story = {
  render: () => <PermissionsScreen enabled={false} {...full} />,
};

export const Loading: Story = {
  render: () => (
    <PermissionsScreen
      enabled
      roles={<RolesView {...rolesProps({ table: rolesTable({ state: "loading" }) })} />}
      assignments={full.assignments}
      diagnostics={full.diagnostics}
    />
  ),
};

export const Empty: Story = {
  render: () => (
    <PermissionsScreen
      enabled
      roles={<RolesView {...rolesProps({ table: rolesTable({ state: "empty" }) })} />}
      assignments={
        <AssignmentsView {...assignmentsProps({ table: bindingsTable({ state: "empty" }) })} />
      }
      diagnostics={
        <PermissionsDiagnosticsView {...diagnosticsProps({ permissions: [], myBindings: [] })} />
      }
    />
  ),
};

export const Error: Story = {
  render: () => (
    <PermissionsScreen
      enabled
      roles={
        <RolesView
          {...rolesProps({
            table: rolesTable({
              state: "error",
              error: new globalThis.Error("upstream timed out"),
            }),
          })}
        />
      }
      assignments={full.assignments}
      diagnostics={full.diagnostics}
    />
  ),
};

export const LongStrings: Story = {
  render: () => (
    <PermissionsScreen
      enabled
      roles={
        <RolesView
          {...rolesProps({
            table: rolesTable({ rows: [LONG_ROLE, ...ROLES], totalCount: 214, hasNext: true }),
            allPermissions: LONG_PERMISSIONS,
          })}
        />
      }
      assignments={
        <AssignmentsView {...assignmentsProps({ table: bindingsTable({ rows: LONG_BINDINGS }) })} />
      }
      diagnostics={
        <PermissionsDiagnosticsView
          {...diagnosticsProps({ permissions: LONG_PERMISSIONS, myBindings: LONG_BINDINGS })}
        />
      }
    />
  ),
};
