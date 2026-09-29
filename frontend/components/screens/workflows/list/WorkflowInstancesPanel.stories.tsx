import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { useState } from "react";

import {
  InstanceAdminControlsView,
  InstanceDetailView,
  WorkflowInstancesPanelView,
} from "./WorkflowInstancesPanel";
import {
  INSTANCE_DETAIL,
  INSTANCES,
  LONG_INSTANCE,
  adminProps,
  detailProps,
  loadError,
  panelProps,
} from "./workflows-list.fixtures";

const meta: Meta = {
  title: "Screens/Workflows/List/WorkflowInstancesPanel",
};
export default meta;

type Story = StoryObj;

/** A selected running instance with admin controls; rows select locally. */
export const Full: Story = {
  render: function Render() {
    const [selected, setSelected] = useState<string | null>(INSTANCES[0].workflowId);
    return (
      <WorkflowInstancesPanelView
        {...panelProps({ selectedWorkflowId: selected, setSelectedWorkflowId: setSelected })}
        detail={
          <InstanceDetailView
            {...detailProps({
              workflowId: selected,
              onClose: () => setSelected(null),
              adminControls: <InstanceAdminControlsView {...adminProps()} />,
            })}
          />
        }
      />
    );
  },
};

export const NothingSelected: Story = {
  render: () => (
    <WorkflowInstancesPanelView
      {...panelProps()}
      detail={<InstanceDetailView {...detailProps({ workflowId: null, detail: null })} />}
    />
  ),
};

export const Loading: Story = {
  render: () => <WorkflowInstancesPanelView {...panelProps({ loading: true, instances: [] })} />,
};

/** No instances under the default filters: the single idle-engine state. */
export const Empty: Story = {
  render: () => (
    <WorkflowInstancesPanelView {...panelProps({ statusFilter: "ALL", instances: [] })} />
  ),
};

export const EmptyFiltered: Story = {
  render: () => (
    <WorkflowInstancesPanelView
      {...panelProps({ typeFilter: "Nope", instances: [] })}
      detail={<InstanceDetailView {...detailProps({ workflowId: null, detail: null })} />}
    />
  ),
};

export const LoadFailed: Story = {
  render: () => (
    <WorkflowInstancesPanelView
      {...panelProps({ instances: [], error: loadError("Temporal frontend unreachable") })}
      detail={<InstanceDetailView {...detailProps({ workflowId: null, detail: null })} />}
    />
  ),
};

export const LongStrings: Story = {
  render: () => (
    <WorkflowInstancesPanelView
      {...panelProps({
        instances: [LONG_INSTANCE, ...INSTANCES],
        selectedWorkflowId: LONG_INSTANCE.workflowId,
      })}
      detail={
        <InstanceDetailView
          {...detailProps({
            workflowId: LONG_INSTANCE.workflowId,
            detail: { ...INSTANCE_DETAIL, instance: LONG_INSTANCE },
          })}
        />
      }
    />
  ),
};

export const DetailLoading: Story = {
  render: () => <InstanceDetailView {...detailProps({ detail: null, loading: true })} />,
};

export const DetailLoadFailed: Story = {
  render: () => (
    <InstanceDetailView
      {...detailProps({ detail: null, error: loadError("instance not found") })}
    />
  ),
};

export const DetailNoHistory: Story = {
  render: () => (
    <InstanceDetailView
      {...detailProps({
        detail: {
          instance: { ...INSTANCE_DETAIL.instance, status: "COMPLETED", durationSeconds: 7260 },
          history: [],
        },
      })}
    />
  ),
};

export const AdminControlsBusy: Story = {
  render: () => (
    <InstanceAdminControlsView {...adminProps({ cancelLoading: true, terminateLoading: true })} />
  ),
};
