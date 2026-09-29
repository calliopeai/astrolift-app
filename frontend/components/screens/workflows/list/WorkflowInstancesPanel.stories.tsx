import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { InstanceAdminControlsView, InstanceDetailView } from "./WorkflowInstancesPanel";
import {
  INSTANCE_DETAIL,
  LONG_INSTANCE,
  adminProps,
  detailProps,
  loadError,
} from "./workflows-list.fixtures";

const meta: Meta = {
  title: "Screens/Workflows/List/WorkflowInstancesPanel",
};
export default meta;

type Story = StoryObj;

/** A running instance with its admin controls, as the list's side sheet shows it. */
export const Full: Story = {
  render: () => (
    <InstanceDetailView
      {...detailProps({ adminControls: <InstanceAdminControlsView {...adminProps()} /> })}
    />
  ),
};

export const LongStrings: Story = {
  render: () => (
    <InstanceDetailView
      {...detailProps({
        workflowId: LONG_INSTANCE.workflowId,
        detail: { ...INSTANCE_DETAIL, instance: LONG_INSTANCE },
      })}
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
