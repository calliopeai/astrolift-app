import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import type { AstroliftDeployment } from "@/graphql/lifecycle/lifecycle.types";

import {
  ACTIONS,
  DEPLOY_DEPLOYING,
  DEPLOY_FAILED,
  DEPLOY_LONG,
  DEPLOY_PENDING_APPROVAL,
  DEPLOY_RUNNING,
  DEPLOY_SUPERSEDED,
  LONG,
} from "./app-deployments-logs.fixtures";
import {
  type ActionTarget,
  type DeploymentActions,
  DeploymentActionDialog,
  DeploymentRowMenuItems,
} from "./DeploymentRowActions";

/** A deployment row's `⋯` menu, and the confirm dialog it opens outside the menu. */
const meta: Meta = { title: "Screens/Apps/Deployments/DeploymentRowActions" };
export default meta;

type Story = StoryObj;

function Menu({
  deployment = DEPLOY_RUNNING,
  actions = ACTIONS,
  appSlug = "storefront",
}: {
  deployment?: AstroliftDeployment;
  actions?: DeploymentActions;
  appSlug?: string;
}) {
  const [target, setTarget] = React.useState<ActionTarget | null>(null);
  return (
    <>
      <DropdownMenu defaultOpen modal={false}>
        <DropdownMenuTrigger asChild>
          <Button variant="outline" size="sm">
            Row actions
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="min-w-40">
          <DeploymentRowMenuItems deployment={deployment} actions={actions} onRequest={setTarget} />
        </DropdownMenuContent>
      </DropdownMenu>
      <DeploymentActionDialog
        target={target}
        appSlug={appSlug}
        onClose={() => setTarget(null)}
        actions={actions}
      />
    </>
  );
}

/** A running deploy: redeploy and roll back. */
export const Full: Story = { render: () => <Menu /> };

export const PendingApproval: Story = {
  render: () => <Menu deployment={DEPLOY_PENDING_APPROVAL} />,
};

export const InFlight: Story = { render: () => <Menu deployment={DEPLOY_DEPLOYING} /> };

export const Failed: Story = { render: () => <Menu deployment={DEPLOY_FAILED} /> };

export const Superseded: Story = { render: () => <Menu deployment={DEPLOY_SUPERSEDED} /> };

/** A mutation in flight: every item disabled. The closest this has to loading. */
export const Busy: Story = { render: () => <Menu actions={{ ...ACTIONS, busy: true }} /> };

/** No permissions: one disabled item says so. The closest to empty. */
export const ReadOnly: Story = {
  render: () => (
    <Menu
      deployment={DEPLOY_FAILED}
      actions={{ ...ACTIONS, canApprove: false, canDeploy: false, canRollback: false }}
    />
  ),
};

/** The confirm dialog open. Failures surface here (the handlers throw); there is no row error. */
export const ConfirmOpen: Story = {
  render: () => (
    <DeploymentActionDialog
      target={{ kind: "abort", deployment: DEPLOY_DEPLOYING }}
      appSlug="storefront"
      onClose={() => {}}
      actions={ACTIONS}
    />
  ),
};

/** Long strings land in the dialog titles. */
export const LongStrings: Story = {
  render: () => (
    <DeploymentActionDialog
      target={{ kind: "redeploy", deployment: { ...DEPLOY_LONG, status: "running" } }}
      appSlug={LONG}
      onClose={() => {}}
      actions={ACTIONS}
    />
  ),
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <Menu deployment={DEPLOY_LONG} />
    </div>
  ),
};
