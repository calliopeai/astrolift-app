import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import type { AstroliftAppEnvironment } from "@/graphql/lifecycle/lifecycle.types";
import type { AstroliftWorkload } from "@/graphql/registry/registry.types";

import {
  ENV_LONG,
  ENV_PROD,
  ENV_STG,
  envControls,
  WORKLOAD_LONG,
  WORKLOADS,
  workloadOps,
} from "./app-controls.fixtures";
import {
  ControlsSectionView,
  EnvironmentControlsView,
  WorkloadOpsRowView,
} from "./ControlsSection";

const meta: Meta = { title: "Screens/Apps/Controls/ControlsSection" };
export default meta;

type Story = StoryObj;

function Section({
  envs,
  workloads = WORKLOADS,
  workloadsLoading = false,
}: {
  envs: AstroliftAppEnvironment[];
  workloads?: AstroliftWorkload[];
  workloadsLoading?: boolean;
}) {
  return (
    <ControlsSectionView
      envs={envs}
      loading={false}
      renderEnvironment={(env) => (
        <EnvironmentControlsView
          {...envControls(env)}
          workloads={workloads}
          workloadsLoading={workloadsLoading}
          renderWorkload={(w) => <WorkloadOpsRowView {...workloadOps(env.name, w)} />}
        />
      )}
    />
  );
}

/** One active env with approvals required, one paused env. */
export const Full: Story = { render: () => <Section envs={[ENV_PROD, ENV_STG]} /> };

export const Loading: Story = {
  render: () => <ControlsSectionView envs={[]} loading renderEnvironment={() => null} />,
};

/** Envs still loading while the workload list is. */
export const WorkloadsLoading: Story = {
  render: () => <Section envs={[ENV_PROD]} workloadsLoading />,
};

export const Empty: Story = {
  render: () => <ControlsSectionView envs={[]} loading={false} renderEnvironment={() => null} />,
};

/** An env with no Deployment-kind workloads. */
export const NoWorkloads: Story = { render: () => <Section envs={[ENV_PROD]} workloads={[]} /> };

/**
 * The section has no error state (query errors are not surfaced; mutation
 * failures are toasts), so this shows the closest real one: every action
 * mid-flight.
 */
export const Busy: Story = {
  render: () => (
    <ControlsSectionView
      envs={[ENV_PROD]}
      loading={false}
      renderEnvironment={(env) => (
        <EnvironmentControlsView
          {...envControls(env)}
          pausing
          deploying
          rebuilding
          workloads={WORKLOADS}
          workloadsLoading={false}
          renderWorkload={(w) => (
            <WorkloadOpsRowView {...workloadOps(env.name, w)} restarting scaling />
          )}
        />
      )}
    />
  ),
};

export const LongStrings: Story = {
  render: () => <Section envs={[ENV_LONG]} workloads={[WORKLOAD_LONG, ...WORKLOADS]} />,
};
