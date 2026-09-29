import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { type ListState, useLocalListState } from "@/components/list/use-list-state";

import {
  HISTORY_ROWS,
  LIST_LONG_ROWS,
  listProps,
  ROWS,
  TRIGGERED_BY_OTHER,
} from "./deployments.fixtures";
import { DEPLOYMENTS_LIST, deploymentsVariables } from "./deployments-list";
import { DeploymentsScreen, type DeploymentsScreenProps } from "./DeploymentsScreen";

/** Fixed for the story: the fixtures are minutes old relative to module load. */
const NOW = new Date().getTime();

const meta: Meta = {
  title: "Screens/Deployments/DeploymentsScreen",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

type Props = Partial<Omit<DeploymentsScreenProps, "list">> & { initial?: Partial<ListState> };

/**
 * The screen over fixture rows, as the server answers the list state: here
 * the story stands in for `astroliftDeploymentsPage`, applying the variables
 * the hook sends (status, and the filter's trigger, who triggered it and the
 * start time).
 */
function Deployments({ initial, rows = ROWS, ...patch }: Props) {
  const list = useLocalListState(DEPLOYMENTS_LIST, initial);
  const v = deploymentsVariables(list.filters, list.state, NOW);
  const after = v.filter?.startedAfter ? Date.parse(v.filter.startedAfter) : null;
  const shown = rows.filter(
    (d) =>
      (!v.statuses || v.statuses.includes(d.status)) &&
      (!v.filter?.triggerKind || v.filter.triggerKind.includes(d.triggerKind)) &&
      (!v.filter?.triggeredBy || d.triggeredByMe) &&
      (after === null || Date.parse(d.startedAt ?? d.createdAt) >= after)
  );
  return <DeploymentsScreen {...listProps({ rows: shown, ...patch })} list={list} />;
}

/** All: a page of rows, newest first, cursor paged. */
export const Full: Story = { render: () => <Deployments /> };

export const Loading: Story = { render: () => <Deployments rows={[]} loading /> };

export const Empty: Story = { render: () => <Deployments rows={[]} totalCount={0} /> };

/** A search or chip that matches nothing: "No deployments match" and Clear. */
export const EmptyFiltered: Story = {
  render: () => <Deployments initial={{ filters: { trigger: "rollback" } }} />,
};

/** Waiting approval with nothing waiting. */
export const EmptyView: Story = {
  render: () => <Deployments rows={HISTORY_ROWS.slice(2)} initial={{ view: "waiting" }} />,
};

export const ErrorState: Story = {
  render: () => <Deployments rows={[]} error={{ message: "Network error: failed to fetch" }} />,
};

/** Mine: the deployments the viewer triggered. */
export const Mine: Story = { render: () => <Deployments initial={{ view: "mine" }} /> };

export const WaitingApproval: Story = {
  render: () => <Deployments initial={{ view: "waiting" }} />,
};

export const Failed: Story = { render: () => <Deployments initial={{ view: "failed" }} /> };

/** Today: started since local midnight. */
export const Today: Story = { render: () => <Deployments initial={{ view: "today" }} /> };

/** App and trigger chips together. */
export const Filtered: Story = {
  render: () => <Deployments initial={{ filters: { app: "storefront", trigger: "push" } }} />,
};

/** Live: two new deployments wait behind the pill; the rows on screen stay put. */
export const NewRows: Story = {
  render: () => <Deployments newRows={{ count: 2, onReveal: () => {} }} />,
};

/** Rows answer the previous search while the next loads. */
export const Refetching: Story = { render: () => <Deployments stale /> };

/** Page two: Newer goes back. */
export const OlderPage: Story = { render: () => <Deployments initial={{ after: "c:25" }} /> };

/** A bulk cancel in flight. */
export const BulkRunning: Story = {
  render: () => <Deployments rows={[ROWS[0], TRIGGERED_BY_OTHER]} bulkRunning />,
};

/** No deploy permissions: no selection column, no lifecycle row actions, no Start. */
export const ReadOnly: Story = {
  render: () => <Deployments canDeploy={false} canApprove={false} canRollback={false} />,
};

/** A 64-char SHA, a long app, workload and image tag. */
export const LongStrings: Story = {
  render: () => <Deployments rows={LIST_LONG_ROWS} totalCount={2} nextCursor={null} />,
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <Deployments />
    </div>
  ),
};
