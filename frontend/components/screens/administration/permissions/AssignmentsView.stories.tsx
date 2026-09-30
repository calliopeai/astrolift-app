import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";

import { type ListState, useLocalListState } from "@/components/list/use-list-state";

import { pageData } from "../access/fixtures";
import { AssignmentsView, type AssignmentsViewProps } from "./AssignmentsView";
import { BINDINGS, LONG_BINDINGS, assignmentsProps } from "./fixtures";
import { ASSIGNMENTS_LIST } from "./permissions-lists";

const meta: Meta = {
  title: "Screens/Administration/Permissions/AssignmentsView",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

function Assignments({
  initial,
  ...props
}: Omit<AssignmentsViewProps, "list"> & { initial?: Partial<ListState> }) {
  const list = useLocalListState(ASSIGNMENTS_LIST, initial);
  return <AssignmentsView list={list} {...props} />;
}

export const Full: Story = {
  render: () => (
    <Assignments {...assignmentsProps({ page: pageData(BINDINGS, { totalCount: 57 }) })} />
  ),
};

/** Mine: the viewer's own bindings and those on the IdP groups they are in. */
export const Mine: Story = {
  render: () => (
    <Assignments
      {...assignmentsProps({ page: pageData(BINDINGS.slice(0, 2)) })}
      initial={{ view: "mine" }}
    />
  ),
};

export const Loading: Story = {
  render: () => (
    <Assignments
      {...assignmentsProps({ page: pageData([], { loading: true }), rolesLoading: true })}
    />
  ),
};

export const Empty: Story = {
  render: () => <Assignments {...assignmentsProps({ page: pageData([]) })} />,
};

export const NoMatches: Story = {
  render: () => (
    <Assignments {...assignmentsProps({ page: pageData([]) })} initial={{ q: "nobody" }} />
  ),
};

export const Error: Story = {
  render: () => (
    <Assignments
      {...assignmentsProps({ page: pageData([], { error: { message: "upstream timed out" } }) })}
    />
  ),
};

/** Two rows selected: the bulk revoke action shows. */
export const WithSelection: Story = {
  render: () => <Assignments {...assignmentsProps()} />,
  play: async ({ canvasElement }) => {
    const boxes = within(canvasElement).getAllByRole("checkbox");
    await userEvent.click(boxes[2]);
    await userEvent.click(boxes[4]);
    await expect(within(canvasElement).getByRole("button", { name: /Revoke 2/ })).toBeVisible();
  },
};

/** A viewer without org.manage_members: no selection column, no bulk action, no row menu. */
export const NoManageAccess: Story = {
  render: () => <Assignments {...assignmentsProps({ canManage: false })} />,
};

export const LongStrings: Story = {
  render: () => (
    <Assignments
      {...assignmentsProps({
        page: pageData([...LONG_BINDINGS, ...BINDINGS], { totalCount: 1_284 }),
      })}
    />
  ),
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }} className="overflow-hidden border">
      <Assignments {...assignmentsProps({ page: pageData([...LONG_BINDINGS, ...BINDINGS]) })} />
    </div>
  ),
};

/** The complete matching-list download is still running; no duplicate export. */
export const ExportingCsv: Story = {
  render: () => <Assignments {...assignmentsProps({ exportingCsv: true })} />,
  play: async ({ canvasElement }) => {
    await userEvent.click(within(canvasElement).getByRole("button", { name: "More list actions" }));
    await expect(
      within(canvasElement.ownerDocument.body).getByRole("menuitem", { name: "Exporting CSV…" })
    ).toHaveAttribute("aria-disabled", "true");
  },
};
