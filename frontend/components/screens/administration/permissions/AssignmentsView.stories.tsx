import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { fakeController } from "@/components/data-table/fixtures";
import type { AstroliftRoleBinding } from "@/graphql/identity/identity.types";

import { AssignmentsView } from "./AssignmentsView";
import { BINDINGS, LONG_BINDINGS, assignmentsProps, fakeSelection } from "./fixtures";

const meta: Meta = {
  title: "Screens/Administration/Permissions/AssignmentsView",
};
export default meta;

type Story = StoryObj;

const table = (patch: Parameters<typeof fakeController<AstroliftRoleBinding>>[0]) =>
  fakeController<AstroliftRoleBinding>({ sort: undefined, sortEnabled: false, ...patch });

export const Full: Story = { render: () => <AssignmentsView {...assignmentsProps()} /> };

export const Loading: Story = {
  render: () => (
    <AssignmentsView
      {...assignmentsProps({ table: table({ state: "loading" }), rolesLoading: true })}
    />
  ),
};

export const Empty: Story = {
  render: () => <AssignmentsView {...assignmentsProps({ table: table({ state: "empty" }) })} />,
};

export const NoMatches: Story = {
  render: () => (
    <AssignmentsView
      {...assignmentsProps({
        table: table({ state: "emptyFiltered", search: "nobody", isFiltered: true }),
      })}
    />
  ),
};

export const Error: Story = {
  render: () => (
    <AssignmentsView
      {...assignmentsProps({
        table: table({ state: "error", error: new globalThis.Error("upstream timed out") }),
      })}
    />
  ),
};

/** Two rows selected: the bulk revoke action shows. */
export const WithSelection: Story = {
  render: () => (
    <AssignmentsView
      {...assignmentsProps({
        table: table({ rows: BINDINGS, totalCount: BINDINGS.length }),
        selection: fakeSelection(["b-2", "b-4"]),
      })}
    />
  ),
};

/** A viewer without org.manage_members: no selection column, no bulk action. */
export const NoManageAccess: Story = {
  render: () => <AssignmentsView {...assignmentsProps({ canManage: false })} />,
};

export const LongStrings: Story = {
  render: () => (
    <AssignmentsView
      {...assignmentsProps({
        table: table({ rows: [...LONG_BINDINGS, ...BINDINGS], totalCount: 1_284, hasNext: true }),
      })}
    />
  ),
};
