import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";

import { type AppDeployStrategyFields, AppDeployStrategyStep } from "./AppDeployStrategyStep";
import {
  APPROVER_DATA,
  APPROVER_LOADING,
  APPROVER_LONG,
  DEPLOY_STRATEGY_STATE,
} from "./apps-wizard-steps-a.fixtures";
import { ApproverSelectorView } from "./ApproverSelector";
import type { ApproverSelectorData } from "./use-approver-selector";

const meta: Meta = { title: "Screens/Apps/New/AppDeployStrategyStep" };
export default meta;

type Story = StoryObj;

/** Holds the wizard fields so every option card and input works. */
function Demo({
  initial,
  approvers = APPROVER_DATA,
}: {
  initial: Partial<AppDeployStrategyFields>;
  approvers?: ApproverSelectorData;
}) {
  const [state, setState] = React.useState<AppDeployStrategyFields>({
    ...DEPLOY_STRATEGY_STATE,
    ...initial,
  });
  return (
    <AppDeployStrategyStep
      state={state}
      setState={setState}
      approverSlot={
        <ApproverSelectorView
          {...approvers}
          value={{
            approverUserIds: state.approverUserIds,
            approverTeamId: state.approverTeamId,
            minimumApprovals: state.minimumApprovals,
          }}
          onChange={(next) => setState((s) => ({ ...s, ...next }))}
          onValidityChange={() => {}}
        />
      }
    />
  );
}

/** Deploy now, auto on push to main (the wizard's default). */
export const Default: Story = { render: () => <Demo initial={{}} /> };

/** The step has no loading state of its own; the approver picker loading is the closest. */
export const Loading: Story = {
  render: () => <Demo initial={{ requiresApproval: true }} approvers={APPROVER_LOADING} />,
};

/** "Skip — configure later": trigger and approval config are hidden. */
export const Skipped: Story = { render: () => <Demo initial={{ deployTiming: "skip" }} /> };

/** No error state exists; an invalid cron expression is the closest (step blocks Next). */
export const InvalidCron: Story = {
  render: () => <Demo initial={{ triggerMode: "cron", cronExpression: "every day" }} />,
};

export const Cron: Story = {
  render: () => <Demo initial={{ triggerMode: "cron", cronExpression: "0 3 * * *" }} />,
};

/** Approval gate on, with two approvers picked. */
export const Full: Story = {
  render: () => (
    <Demo
      initial={{
        deployTiming: "later",
        triggerMode: "manual",
        triggerFirstDeploy: false,
        requiresApproval: true,
        approverUserIds: ["u-1", "u-2"],
        minimumApprovals: 2,
      }}
    />
  ),
};

export const LongStrings: Story = {
  render: () => (
    <Demo
      initial={{
        deployBranch: "release/2026-09-quarterly-hardening-and-dependency-refresh-for-storefront",
        defaultBranch: "release/2026-09-quarterly-hardening",
        requiresApproval: true,
        approverUserIds: ["u-long"],
      }}
      approvers={APPROVER_LONG}
    />
  ),
};
