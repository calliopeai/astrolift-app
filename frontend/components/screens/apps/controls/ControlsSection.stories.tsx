import { expect, within } from "storybook/test";
import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { NextIntlClientProvider } from "next-intl";
import ja from "@/messages/ja.json";

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
      workloads={workloads}
      workloadsLoading={workloadsLoading}
      renderWorkload={(w) => <WorkloadOpsRowView {...workloadOps(w)} />}
      renderEnvironment={(env) => <EnvironmentControlsView {...envControls(env)} />}
    />
  );
}

/** One active env with approvals required, one paused env. */
export const Full: Story = {
  render: () => <Section envs={[ENV_PROD, ENV_STG]} />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByRole("heading", { name: "Primary environment" })).toBeVisible();
    await expect(canvas.getAllByRole("button", { name: "Rolling restart" })).toHaveLength(
      WORKLOADS.length
    );
  },
};

export const Loading: Story = {
  render: () => (
    <ControlsSectionView
      envs={[]}
      loading
      workloads={[]}
      workloadsLoading
      renderWorkload={() => null}
      renderEnvironment={() => null}
    />
  ),
};

/** Envs still loading while the workload list is. */
export const WorkloadsLoading: Story = {
  render: () => <Section envs={[ENV_PROD]} workloadsLoading />,
};

export const Empty: Story = {
  render: () => (
    <ControlsSectionView
      envs={[]}
      loading={false}
      workloads={[]}
      workloadsLoading={false}
      renderWorkload={() => null}
      renderEnvironment={() => null}
    />
  ),
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
      workloads={WORKLOADS}
      workloadsLoading={false}
      renderWorkload={(w) => <WorkloadOpsRowView {...workloadOps(w)} restarting scaling />}
      renderEnvironment={(env) => (
        <EnvironmentControlsView {...envControls(env)} pausing deploying rebuilding />
      )}
    />
  ),
};

export const LongStrings: Story = {
  render: () => <Section envs={[ENV_LONG]} workloads={[WORKLOAD_LONG, ...WORKLOADS]} />,
};

export const RestrictedWorkload: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByRole("button", { name: "Rolling restart" })).toBeDisabled();
    await expect(canvas.getByRole("button", { name: "Increase replicas" })).toBeDisabled();
    await expect(canvas.getByText(/Production policy requires reauthentication/)).toBeVisible();
  },
  render: () => (
    <Section
      envs={[ENV_PROD, ENV_STG]}
      workloads={[
        {
          ...WORKLOADS[0],
          viewerCan: {
            restart: {
              allowed: false,
              code: "PERMISSION_DENIED",
              reason: "Production policy requires reauthentication",
            },
            scale: {
              allowed: false,
              code: "PERMISSION_DENIED",
              reason: "No role binding grants this permission",
            },
          },
        },
      ]}
    />
  ),
};
export const ScaleOnly: Story = {
  render: () => (
    <Section
      envs={[ENV_PROD]}
      workloads={[
        {
          ...WORKLOADS[0],
          viewerCan: {
            restart: { allowed: false, code: "PERMISSION_DENIED", reason: "Restart is restricted" },
            scale: { allowed: true, code: "", reason: "" },
          },
        },
      ]}
    />
  ),
};

export const JapaneseEnvironmentWidth768: Story = {
  render: () => (
    <NextIntlClientProvider locale="ja" messages={ja}>
      <div style={{ width: 768 }}>
        <EnvironmentControlsView {...envControls(ENV_PROD)} />
      </div>
    </NextIntlClientProvider>
  ),
};
