import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

import { LONG } from "./app-workloads.fixtures";
import { WorkloadRowActions, type WorkloadRowActionsProps } from "./WorkloadRowActions";

/** A workload row's `⋯` items: logs, and Run now per environment for a CronJob. */
const meta: Meta = { title: "Screens/Apps/Workloads/WorkloadRowActions" };
export default meta;

type Story = StoryObj;

const BASE: WorkloadRowActionsProps = {
  workload: { slug: "nightly-invoices", kind: "cronjob" },
  logsHref: "#logs",
  environments: [
    { id: "env-1", name: "production" },
    { id: "env-2", name: "staging" },
  ],
  pendingSlug: null,
  canRun: true,
  onRun: () => {},
};

function Menu(props: Partial<WorkloadRowActionsProps>) {
  return (
    <DropdownMenu defaultOpen modal={false}>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" size="sm">
          Row actions
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="min-w-40">
        <WorkloadRowActions {...BASE} {...props} />
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/** A CronJob: logs, then Run now in each environment. */
export const Full: Story = { render: () => <Menu /> };

/** A dispatch in flight: that job's items spin and disable. The closest to loading. */
export const Running: Story = { render: () => <Menu pendingSlug="nightly-invoices" /> };

/** No environments yet: Run now says why it is not offered. The closest to empty. */
export const NoEnvironments: Story = { render: () => <Menu environments={[]} /> };

/** A Deployment, or a viewer without app.deploy: logs only. Failures surface as toasts. */
export const NotRunnable: Story = {
  render: () => <Menu workload={{ slug: "api", kind: "deployment" }} />,
};

export const LongStrings: Story = {
  render: () => (
    <Menu
      workload={{ slug: LONG, kind: "cronjob" }}
      environments={[{ id: "env-long", name: `preview-${LONG}` }]}
    />
  ),
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <Menu />
    </div>
  ),
};
