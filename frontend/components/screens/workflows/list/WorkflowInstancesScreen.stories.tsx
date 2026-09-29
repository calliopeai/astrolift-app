import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { InstanceDetailView, WorkflowInstancesPanelView } from "./WorkflowInstancesPanel";
import { WorkflowInstancesScreen } from "./WorkflowInstancesScreen";
import { LONG_INSTANCE, detailProps, loadError, panelProps } from "./workflows-list.fixtures";

const meta: Meta = {
  title: "Screens/Workflows/List/WorkflowInstancesScreen",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

export const Full: Story = {
  render: () => (
    <WorkflowInstancesScreen
      canView
      panel={
        <WorkflowInstancesPanelView
          {...panelProps({ selectedWorkflowId: "deploy-app-billing-api-7f3c" })}
          detail={<InstanceDetailView {...detailProps()} />}
        />
      }
    />
  ),
};

export const Loading: Story = {
  render: () => <WorkflowInstancesScreen canView={false} loading panel={null} />,
};

/** The engine is idle. */
export const Empty: Story = {
  render: () => (
    <WorkflowInstancesScreen
      canView
      panel={
        <WorkflowInstancesPanelView
          {...panelProps({ instances: [], statusFilter: "ALL" })}
          detail={null}
        />
      }
    />
  ),
};

export const ErrorState: Story = {
  render: () => (
    <WorkflowInstancesScreen
      canView
      panel={
        <WorkflowInstancesPanelView
          {...panelProps({ instances: [], error: loadError() })}
          detail={null}
        />
      }
    />
  ),
};

/** Without `audit_log.read`: the permission, and no panel mounted. */
export const NoAccess: Story = {
  render: () => <WorkflowInstancesScreen canView={false} panel={null} />,
};

export const LongStrings: Story = {
  render: () => (
    <WorkflowInstancesScreen
      canView
      panel={
        <WorkflowInstancesPanelView {...panelProps({ instances: [LONG_INSTANCE] })} detail={null} />
      }
    />
  ),
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <WorkflowInstancesScreen
        canView
        panel={<WorkflowInstancesPanelView {...panelProps()} detail={null} />}
      />
    </div>
  ),
};
