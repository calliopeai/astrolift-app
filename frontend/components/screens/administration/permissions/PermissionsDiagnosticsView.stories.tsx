import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { LONG_BINDINGS, LONG_PERMISSIONS, diagnosticsProps } from "./fixtures";
import { PermissionsDiagnosticsView } from "./PermissionsDiagnosticsView";

const meta: Meta = {
  title: "Screens/Administration/Permissions/PermissionsDiagnosticsView",
};
export default meta;

type Story = StoryObj;

export const Full: Story = {
  render: () => <PermissionsDiagnosticsView {...diagnosticsProps()} />,
};

export const Loading: Story = {
  render: () => (
    <PermissionsDiagnosticsView
      {...diagnosticsProps({
        me: null,
        permissions: [],
        permissionsLoading: true,
        myBindings: [],
        bindingsLoading: true,
      })}
    />
  ),
};

/** No permissions and no bindings: the superuser / nothing-bound case. */
export const Empty: Story = {
  render: () => (
    <PermissionsDiagnosticsView {...diagnosticsProps({ permissions: [], myBindings: [] })} />
  ),
};

/**
 * The queries failed. The view has no error state of its own: with no data
 * it reads as empty, and the account card shows dashes.
 */
export const Error: Story = {
  render: () => (
    <PermissionsDiagnosticsView
      {...diagnosticsProps({ me: null, permissions: [], myBindings: [] })}
    />
  ),
};

export const LongStrings: Story = {
  render: () => (
    <PermissionsDiagnosticsView
      {...diagnosticsProps({
        me: {
          id: "u-7f3c2a10-4b1e-4d2a-9c8f-0e1d2c3b4a59",
          profile: { id: "p-long", username: "maximiliana.vandersloot-oyelaran" },
          modules: [],
        },
        permissions: LONG_PERMISSIONS,
        myBindings: LONG_BINDINGS,
      })}
    />
  ),
};
