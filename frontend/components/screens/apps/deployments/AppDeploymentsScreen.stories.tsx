import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";
import { expect, userEvent, within } from "storybook/test";

import {
  ACTIONS,
  COMPARE,
  DEPLOY_LONG,
  DEPLOY_RUNNING,
  DETAIL,
  DETAIL_LONG,
  LONG,
  SCREEN,
} from "./app-deployments-logs.fixtures";
import type { StatusBucket } from "./app-deployments-format";
import { AppDeploymentsScreen, type AppDeploymentsScreenProps } from "./AppDeploymentsScreen";
import { CompareDeploymentsSheetView } from "./CompareDeploymentsSheet";
import { DeploymentExpandPanelView } from "./DeploymentExpandPanel";
import { DeploymentRowActionsView } from "./DeploymentRowActions";

const meta: Meta = {
  title: "Screens/Apps/Deployments/AppDeploymentsScreen",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

const slots: Pick<
  AppDeploymentsScreenProps,
  "renderRowActions" | "renderExpandPanel" | "renderCompare"
> = {
  renderRowActions: (d) => <DeploymentRowActionsView {...ACTIONS} deployment={d} />,
  renderExpandPanel: (d, onClose) => (
    <DeploymentExpandPanelView
      {...(d.id === DEPLOY_LONG.id ? DETAIL_LONG : DETAIL)}
      deployment={d}
      onClose={onClose}
    />
  ),
  renderCompare: (args) => <CompareDeploymentsSheetView {...COMPARE} {...args} />,
};

/** Holds the URL-backed filters and open row in state, the way the hook does. */
function Controlled(props: AppDeploymentsScreenProps) {
  const [statusBucket, setStatusBucket] = React.useState<StatusBucket>(props.statusBucket);
  const [envFilter, setEnvFilter] = React.useState(props.envFilter);
  const [search, setSearch] = React.useState(props.search);
  const [openId, setOpenId] = React.useState<string | null>(props.openId);
  return (
    <AppDeploymentsScreen
      {...props}
      {...slots}
      statusBucket={statusBucket}
      setStatusBucket={setStatusBucket}
      envFilter={envFilter}
      setEnvFilter={setEnvFilter}
      search={search}
      setSearch={setSearch}
      openId={openId}
      setOpenId={setOpenId}
      toggleOpen={(id) => setOpenId((cur) => (cur === id ? null : id))}
    />
  );
}

/** Stats, filters, every row state, and the running deploy opened. */
export const Full: Story = {
  render: () => <Controlled {...SCREEN} openId={DEPLOY_RUNNING.id} />,
};

/** Selecting two rows enables Compare. */
export const CompareSelection: Story = {
  render: () => <Controlled {...SCREEN} />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    const boxes = await canvas.findAllByRole("checkbox", { name: /^Select sha-/ });
    await userEvent.click(boxes[0]);
    await userEvent.click(boxes[1]);
    await expect(canvas.getByText("2 selected")).toBeInTheDocument();
  },
};

export const Loading: Story = {
  render: () => <AppDeploymentsScreen {...SCREEN} app={null} loading />,
};

/** The app loaded; its deployments are still on the way. */
export const DeploymentsLoading: Story = {
  render: () => <AppDeploymentsScreen {...SCREEN} deployments={[]} deploymentsLoading />,
};

export const Empty: Story = {
  render: () => <AppDeploymentsScreen {...SCREEN} deployments={[]} />,
};

/** Filters match nothing. */
export const NoMatches: Story = {
  render: () => <AppDeploymentsScreen {...SCREEN} {...slots} search="no-such-tag" />,
};

/**
 * The screen has no error state of its own: a failed app query lands on
 * not found, a failed deployments query on the empty table. Not found is
 * the closest.
 */
export const NotFound: Story = {
  render: () => <AppDeploymentsScreen {...SCREEN} slug="no-such-app" app={null} />,
};

export const LongStrings: Story = {
  render: () => (
    <Controlled
      {...SCREEN}
      app={{ name: LONG, slug: LONG }}
      deployments={[DEPLOY_LONG, ...SCREEN.deployments]}
      environments={[{ name: "prod" }, { name: DEPLOY_LONG.environmentName }]}
      openId={DEPLOY_LONG.id}
    />
  ),
};
