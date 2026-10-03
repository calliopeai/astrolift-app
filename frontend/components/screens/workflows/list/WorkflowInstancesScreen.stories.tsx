import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { useState } from "react";
import { expect, within } from "storybook/test";

import { type ListState, useLocalListState } from "@/components/list/use-list-state";
import type { WorkflowInstance } from "@/graphql/workflows/workflows.types";

import { selectInstances, WORKFLOW_INSTANCES_LIST } from "./workflow-instances-list";
import { InstanceAdminControlsView, InstanceDetailView } from "./WorkflowInstancesPanel";
import {
  WorkflowInstancesScreen,
  type WorkflowInstancesListProps,
} from "./WorkflowInstancesScreen";
import {
  INSTANCE_DETAIL,
  INSTANCES,
  LONG_INSTANCE,
  adminProps,
  detailProps,
} from "./workflows-list.fixtures";

/** Agents › Workflows › Platform instances, on the shared list (spec 44 §5.1). */
const meta: Meta = {
  title: "Screens/Workflows/List/WorkflowInstancesScreen",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

type Props = Partial<
  Omit<WorkflowInstancesListProps, "list" | "rows" | "totalCount" | "selectedWorkflowId">
> & {
  instances?: WorkflowInstance[];
  initial?: Partial<ListState>;
  selected?: string | null;
};

/**
 * The list in memory, the rows selected as the hook selects them; a row
 * opens its instance in the side sheet, which the story holds in state.
 */
function Screen({ instances = INSTANCES, initial, selected = null, ...props }: Props) {
  const list = useLocalListState(WORKFLOW_INSTANCES_LIST, initial);
  const [open, setOpen] = useState<string | null>(selected);
  const { rows, totalCount } = selectInstances(instances, {
    filters: list.filters,
    q: list.state.q,
    sort: list.state.sort,
    page: list.state.page,
    pageSize: list.state.pageSize,
  });
  const openInstance = instances.find((i) => i.workflowId === open);
  return (
    <WorkflowInstancesScreen
      access="granted"
      list={list}
      rows={rows}
      totalCount={totalCount}
      loading={false}
      stale={false}
      error={null}
      onRetry={() => {}}
      instanceHref={(i) => `?instance=${encodeURIComponent(i.workflowId)}`}
      selectedWorkflowId={open}
      selectedRunId={openInstance?.runId ?? null}
      onCloseInstance={() => setOpen(null)}
      detail={
        openInstance ? (
          <InstanceDetailView
            {...detailProps({
              workflowId: openInstance.workflowId,
              detail: { ...INSTANCE_DETAIL, instance: openInstance },
              onClose: () => setOpen(null),
              adminControls: <InstanceAdminControlsView {...adminProps()} />,
            })}
          />
        ) : null
      }
      {...props}
    />
  );
}

/** The server page, newest first, with cursor continuation guidance. */
export const Full: Story = {
  render: () => <Screen />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByText("deploy-app-billing-api-7f3c")).toBeInTheDocument();
    await expect(canvas.getByText(/Temporal pages newest first/)).toBeInTheDocument();
    await expect(canvas.getByRole("link", { name: "Running" })).toBeInTheDocument();
  },
};

/** The Running view: the old Running tab's question. */
export const RunningView: Story = { render: () => <Screen initial={{ view: "running" }} /> };

/** An instance open in the side sheet, with cancel and terminate. */
export const InstanceOpen: Story = {
  render: () => <Screen selected={INSTANCES[0].workflowId} />,
};

/** Sorted by how long each took. */
export const SortedByDuration: Story = {
  render: () => <Screen initial={{ sort: [{ key: "duration", dir: "desc" }] }} />,
};

export const Loading: Story = {
  render: () => <Screen instances={[]} loading />,
};

/** The engine is idle, or Temporal is not enabled. */
export const Empty: Story = { render: () => <Screen instances={[]} /> };

/** A type chip that matches nothing. */
export const EmptyFiltered: Story = {
  render: () => <Screen initial={{ filters: { type: "NopeWorkflow" } }} />,
};

export const ErrorState: Story = {
  render: () => <Screen instances={[]} error={{ message: "Temporal frontend unreachable" }} />,
};

/** The permission set is still loading. */
export const PermissionsLoading: Story = {
  render: () => <WorkflowInstancesScreen access="loading" />,
};

/** Without `audit_log.read`: the permission, and no list mounted. */
export const NoAccess: Story = {
  render: () => <WorkflowInstancesScreen access="denied" />,
  play: async ({ canvasElement }) => {
    await expect(within(canvasElement).queryByRole("table")).toBeNull();
  },
};

export const LongStrings: Story = {
  render: () => <Screen instances={[LONG_INSTANCE, ...INSTANCES]} />,
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <Screen instances={[LONG_INSTANCE, ...INSTANCES]} />
    </div>
  ),
};
