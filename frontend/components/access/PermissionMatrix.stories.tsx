import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { NextIntlClientProvider } from "next-intl";
import ja from "@/messages/ja.json";
import * as React from "react";

import { ASTROLIFT_PERMISSIONS } from "@/lib/permissions/permissions.generated";

import { LONG, RELEASE_CAPTAIN, TEAM_DEV, VIEWER } from "./fixtures";
import { PermissionMatrix } from "./PermissionMatrix";

/**
 * A role's permissions as areas × verbs. Read-only by default, editable with
 * `onChange`, and a diff against another set with `base`.
 */
const meta: Meta<typeof PermissionMatrix> = {
  title: "Access/PermissionMatrix",
  component: PermissionMatrix,
  parameters: { layout: "padded" },
  args: { permissions: TEAM_DEV.permissions },
  decorators: [(Story) => <div className="max-w-4xl">{Story()}</div>],
};
export default meta;

type Story = StoryObj<typeof PermissionMatrix>;

/** Read-only: only resources with something held show. */
export const ReadOnly: Story = {};

export const ReadOnlyShowEmpty: Story = {
  args: { permissions: VIEWER.permissions, showEmpty: true },
};

/** Everything in the catalog, every area open. */
export const Full: Story = {
  args: { permissions: ASTROLIFT_PERMISSIONS },
};

function Editable({ start, base }: { start: readonly string[]; base?: readonly string[] }) {
  const [perms, setPerms] = React.useState<string[]>([...start]);
  return (
    <PermissionMatrix
      permissions={perms}
      onChange={setPerms}
      base={base}
      baseLabel="Team developer"
    />
  );
}

/** A custom role in edit: a cell, a row or an area at once. */
export const EditableMatrix: Story = {
  render: () => <Editable start={RELEASE_CAPTAIN.permissions} />,
};

/** Read-only diff against the role it came from. */
export const Diff: Story = {
  args: {
    permissions: RELEASE_CAPTAIN.permissions,
    base: TEAM_DEV.permissions,
    baseLabel: "Team developer",
  },
};

export const EditableWithDiff: Story = {
  render: () => <Editable start={RELEASE_CAPTAIN.permissions} base={TEAM_DEV.permissions} />,
};

export const Empty: Story = { args: { permissions: [] } };

/** The catalog has not loaded. */
export const NoCatalog: Story = { args: { permissions: [], catalog: [] } };

const LONG_RESOURCE = LONG.replace(/-/g, "_");

/** Resources outside the known areas fall into Other; long slugs truncate. */
export const LongStrings: Story = {
  args: {
    catalog: [
      ...ASTROLIFT_PERMISSIONS,
      `${LONG_RESOURCE}.read`,
      `${LONG_RESOURCE}.${LONG_RESOURCE}`,
    ],
    permissions: [`${LONG_RESOURCE}.read`, `${LONG_RESOURCE}.${LONG_RESOURCE}`, "app.read"],
  },
};

export const Width768: Story = {
  decorators: [
    (Story) => (
      <div style={{ width: 768 }} className="overflow-hidden border p-4">
        {Story()}
      </div>
    ),
  ],
  render: () => <Editable start={RELEASE_CAPTAIN.permissions} base={TEAM_DEV.permissions} />,
};

export const Japanese: Story = {
  args: { permissions: TEAM_DEV.permissions, base: VIEWER.permissions, baseLabel: "Original role" },
  decorators: [
    (Story) => (
      <NextIntlClientProvider locale="ja" messages={ja}>
        <Story />
      </NextIntlClientProvider>
    ),
  ],
};
