import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, fn, userEvent, within } from "storybook/test";

import { type ListState, useLocalListState } from "@/components/list/use-list-state";

import { DISABLED_WORKFLOW, LONG_WORKFLOW, WORKFLOW } from "./workflow-detail-b.fixtures";
import {
  selectTriggers,
  type TriggerRow,
  triggerRows,
  WORKFLOW_TRIGGERS_LIST,
} from "./workflow-triggers-list";
import { WorkflowTriggersView, type WorkflowTriggersViewProps } from "./WorkflowTriggers";

/** A workflow's Triggers tab: its triggers as an embedded list (spec 44 §5.1, §5.2). */
const meta: Meta = {
  title: "Screens/Workflows/Detail/WorkflowTriggers",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

type Props = Partial<Omit<WorkflowTriggersViewProps, "list" | "rows" | "totalCount">> & {
  triggers?: TriggerRow[];
  initial?: Partial<ListState>;
};

/** The list in memory, the rows selected as the hook selects them. */
function Tab({ triggers = triggerRows(WORKFLOW), initial, ...props }: Props) {
  const list = useLocalListState(WORKFLOW_TRIGGERS_LIST, initial);
  const { rows, totalCount } = selectTriggers(triggers, {
    filters: list.filters,
    q: list.state.q,
    sort: list.state.sort,
    page: list.state.page,
    pageSize: list.state.pageSize,
  });
  return (
    <WorkflowTriggersView
      list={list}
      rows={rows}
      totalCount={totalCount}
      canManage
      togglingId={null}
      onToggle={() => {}}
      {...props}
    />
  );
}

const onToggle = fn();

/** A schedule, enabled: one row, with its toggle. The frame covers loading and errors. */
export const Full: Story = {
  render: () => <Tab onToggle={onToggle} />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByText("0 4 * * *")).toBeInTheDocument();
    await expect(canvas.getByText(/holds one trigger today/)).toBeInTheDocument();
    await userEvent.click(canvas.getByRole("button", { name: "Disable" }));
    await expect(onToggle).toHaveBeenCalled();
  },
};

/** The toggle is in flight. */
export const Toggling: Story = {
  render: () => <Tab togglingId={triggerRows(WORKFLOW)[0].id} />,
};

/** Manual trigger, disabled. */
export const Disabled: Story = { render: () => <Tab triggers={triggerRows(DISABLED_WORKFLOW)} /> };

/** A schedule and a webhook, for when a workflow holds several triggers. */
export const Several: Story = {
  render: () => (
    <Tab
      triggers={[
        ...triggerRows(WORKFLOW),
        { id: "nightly-sync:webhook", kind: "webhook", schedule: null, isEnabled: true },
      ]}
    />
  ),
};

/** No `workflow.update`: the toggle column is gone. Mutation errors surface as toasts. */
export const ReadOnly: Story = {
  render: () => <Tab canManage={false} />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.queryByRole("button", { name: "Disable" })).toBeNull();
  },
};

/** A definition opened directly runs on demand: the empty state. */
export const Definition: Story = {
  render: () => <Tab triggers={[]} />,
  play: async ({ canvasElement }) => {
    await expect(within(canvasElement).getByText("Runs on demand")).toBeInTheDocument();
  },
};

/** The Disabled view with an enabled trigger: the view's own empty state. */
export const EmptyView: Story = {
  render: () => <Tab initial={{ view: "disabled" }} />,
};

/** A search that matches nothing. */
export const EmptyFiltered: Story = {
  render: () => <Tab initial={{ q: "webhook" }} />,
};

export const LongStrings: Story = { render: () => <Tab triggers={triggerRows(LONG_WORKFLOW)} /> };

export const At768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <Tab triggers={triggerRows(LONG_WORKFLOW)} />
    </div>
  ),
};
