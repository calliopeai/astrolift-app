import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";

import { type ListState, useLocalListState } from "@/components/list/use-list-state";

import { PendingDeploymentsView } from "../controls/PendingDeployments";
import { PENDING } from "../controls/app-controls.fixtures";
import { appDeploymentsList } from "./app-deployments-list";
import {
  ACTIONS,
  COMPARE,
  DEPLOY_LONG,
  DEPLOYMENTS,
  SCREEN,
} from "./app-deployments-logs.fixtures";
import { AppDeploymentsScreen, type AppDeploymentsScreenProps } from "./AppDeploymentsScreen";
import { CompareDeploymentsSheetView } from "./CompareDeploymentsSheet";
import { DeploymentActionDialog, DeploymentRowMenuItems } from "./DeploymentRowActions";

/** An app's Deployments tab: the embedded list (spec 44 §4.4, §5.1). */
const meta: Meta = {
  title: "Screens/Apps/Deployments/AppDeploymentsScreen",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

const DEF = appDeploymentsList(["prod", "stg"]);

type Props = Partial<Omit<AppDeploymentsScreenProps, "list">> & { initial?: Partial<ListState> };

function Deployments({ initial, ...props }: Props) {
  const list = useLocalListState(DEF, initial);
  return (
    <AppDeploymentsScreen
      {...SCREEN}
      list={list}
      renderRowActions={(d, request) => (
        <DeploymentRowMenuItems deployment={d} actions={ACTIONS} onRequest={request} />
      )}
      renderActionDialog={(target, onClose) => (
        <DeploymentActionDialog
          target={target}
          appSlug="storefront"
          onClose={onClose}
          actions={ACTIONS}
        />
      )}
      renderCompare={(args) => <CompareDeploymentsSheetView {...COMPARE} {...args} />}
      {...props}
    />
  );
}

/** Every row state, newest first, with the view picker in the filter bar. */
export const Full: Story = { render: () => <Deployments /> };

/** Approvals waiting on the viewer sit in a panel above the list. */
export const WithApprovals: Story = {
  render: () => (
    <Deployments approvals={<PendingDeploymentsView {...PENDING} onlyForApprovers />} />
  ),
};

/** Selecting two rows enables Compare. */
export const CompareSelection: Story = {
  render: () => <Deployments />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    const boxes = await canvas.findAllByRole("checkbox", { name: /select/i });
    // The first box is the page's select-all; pick two rows.
    await userEvent.click(boxes[1]);
    await userEvent.click(boxes[2]);
    await expect(canvas.getByRole("button", { name: /^Compare$/ })).toBeEnabled();
  },
};

export const Loading: Story = {
  render: () => <Deployments rows={[]} loading nextCursor={null} totalCount={null} />,
};

export const Empty: Story = {
  render: () => <Deployments rows={[]} nextCursor={null} totalCount={0} />,
};

/** A view with nothing in it: the view's own empty copy, not the create action. */
export const EmptyView: Story = {
  render: () => <Deployments rows={[]} nextCursor={null} initial={{ view: "waiting" }} />,
};

/** Chips and search match nothing. */
export const EmptyFiltered: Story = {
  render: () => (
    <Deployments
      rows={[]}
      nextCursor={null}
      initial={{ q: "no-such-tag", filters: { env: "stg" } }}
    />
  ),
};

export const Error: Story = {
  render: () => (
    <Deployments
      rows={[]}
      error={{ message: "upstream timed out after 30s (astroliftDeploymentsPage)" }}
    />
  ),
};

/** Mine: the deployments the viewer triggered, counted by the server. */
export const Mine: Story = {
  render: () => (
    <Deployments
      rows={DEPLOYMENTS.slice(0, 2).map((d) => ({ ...d, triggeredByMe: true }))}
      totalCount={2}
      initial={{ view: "mine" }}
    />
  ),
};

export const NewRows: Story = {
  render: () => <Deployments newRows={{ count: 3, onReveal: () => {} }} />,
};

/** A 64-char SHA, a 200-char tag and an unbroken URL never widen the page. */
export const LongStrings: Story = {
  render: () => (
    <Deployments
      rows={[
        {
          ...DEPLOY_LONG,
          commitSha: "5f70bf18a086007016e948b04aed3b82103a36bea41755b6cddfaf10ace3c6ef",
          commitMessage: `https://example.com/${"a".repeat(200)}`,
        },
        ...DEPLOYMENTS,
      ]}
    />
  ),
};

/** The narrowest the web console goes (spec 44 §6): the table scrolls in its frame. */
export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }} className="overflow-hidden border">
      <Deployments rows={[DEPLOY_LONG, ...DEPLOYMENTS]} />
    </div>
  ),
};
