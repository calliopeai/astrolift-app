import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { useLocalListState } from "@/components/list/use-list-state";
import { WORKFLOW_INSTANCES_LIST } from "@/components/screens/workflows/list/workflow-instances-list";
import {
  INSTANCES,
  LONG_INSTANCE,
} from "@/components/screens/workflows/list/workflows-list.fixtures";
import type { WorkflowInstance } from "@/graphql/workflows/workflows.types";

import { PlatformActivityScreen } from "./PlatformActivityScreen";

const meta: Meta = {
  title: "Screens/PlatformActivity/PlatformActivityScreen",
  parameters: { layout: "fullscreen" },
};
export default meta;
type Story = StoryObj;

function Screen({
  rows = INSTANCES,
  loading = false,
  error = null,
}: {
  rows?: WorkflowInstance[];
  loading?: boolean;
  error?: { message: string } | null;
}) {
  const list = useLocalListState(WORKFLOW_INSTANCES_LIST);
  return (
    <PlatformActivityScreen
      access="granted"
      list={list}
      rows={rows}
      totalCount={rows.length}
      loading={loading}
      stale={false}
      error={error}
      onRetry={() => {}}
      instanceHref={(i) => `?instance=${encodeURIComponent(i.workflowId)}`}
      selectedWorkflowId={null}
      onCloseInstance={() => {}}
      detail={null}
    />
  );
}

export const Full: Story = { render: () => <Screen /> };
export const Loading: Story = { render: () => <Screen rows={[]} loading /> };
export const Empty: Story = { render: () => <Screen rows={[]} /> };
export const Error: Story = {
  render: () => (
    <Screen rows={[]} error={{ message: "The workflow engine could not be reached." }} />
  ),
};
export const Denied: Story = { render: () => <PlatformActivityScreen access="denied" /> };
export const LongStrings: Story = { render: () => <Screen rows={[LONG_INSTANCE]} /> };
export const Narrow: Story = {
  ...Full,
  globals: { viewport: { value: "tablet", isRotated: false } },
};
