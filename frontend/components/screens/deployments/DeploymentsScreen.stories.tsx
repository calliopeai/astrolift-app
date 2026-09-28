import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import {
  HISTORY_ROWS,
  LIST_LONG_ROWS,
  listProps,
  selectionOf,
  START,
  TRIGGERED_BY_OTHER,
} from "./deployments.fixtures";
import { DeploymentsScreen } from "./DeploymentsScreen";
import { StartDeploymentSheet } from "./StartDeploymentSheet";

const meta: Meta = {
  title: "Screens/Deployments/DeploymentsScreen",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

/** The Active tab with a page of rows and badge counts. */
export const Full: Story = { render: () => <DeploymentsScreen {...listProps()} /> };

export const Loading: Story = {
  render: () => (
    <DeploymentsScreen {...listProps({ tabCounts: undefined }, { rows: [], state: "loading" })} />
  ),
};

export const Empty: Story = {
  render: () => (
    <DeploymentsScreen
      {...listProps(
        {
          tabCounts: {
            active: { totalCount: 0 },
            previews: { totalCount: 0 },
            pending: { totalCount: 0 },
            history: { totalCount: 0 },
          },
        },
        { rows: [], totalCount: 0, state: "empty" }
      )}
    />
  ),
};

/** The search excluded every row in the tab. */
export const EmptyFiltered: Story = {
  render: () => (
    <DeploymentsScreen
      {...listProps({}, { rows: [], totalCount: 0, state: "emptyFiltered", search: "nope" })}
    />
  ),
};

export const LoadFailed: Story = {
  render: () => (
    <DeploymentsScreen
      {...listProps(
        {},
        { rows: [], state: "error", error: new Error("Network error: failed to fetch") }
      )}
    />
  ),
};

export const History: Story = {
  render: () => (
    <DeploymentsScreen {...listProps({ tab: "history" }, { rows: HISTORY_ROWS, totalCount: 38 })} />
  ),
};

/** A signal tab: a gateway placeholder into the fleet explorer, no rows. */
export const MetricsTab: Story = {
  render: () => <DeploymentsScreen {...listProps({ tab: "metrics" })} />,
};

/** An in-flight and a terminal row selected: both bulk CTAs disabled with a hint. */
export const MixedSelection: Story = {
  render: () => {
    const rows = listProps().table.rows;
    const picked = [rows[0], rows[4]];
    return (
      <DeploymentsScreen
        {...listProps({
          selection: selectionOf(picked.map((d) => d.id)),
          selectedDeploys: picked,
        })}
      />
    );
  },
};

/** Two in-flight rows selected while a bulk abort runs. */
export const BulkRunning: Story = {
  render: () => {
    const picked = [listProps().table.rows[0], TRIGGERED_BY_OTHER];
    return (
      <DeploymentsScreen
        {...listProps({
          selection: selectionOf(picked.map((d) => d.id)),
          selectedDeploys: picked,
          bulkRunning: true,
        })}
      />
    );
  },
};

/** No deploy permissions: no selection column, no row actions but view and more. */
export const ReadOnly: Story = {
  render: () => (
    <DeploymentsScreen
      {...listProps({
        canDeploy: false,
        canApprove: false,
        canRollback: false,
        hasAnyAction: false,
      })}
    />
  ),
};

export const LongStrings: Story = {
  render: () => <DeploymentsScreen {...listProps({}, { rows: LIST_LONG_ROWS, totalCount: 2 })} />,
};

/** The start sheet open over the list, as the page wires it. */
export const StartSheetOpen: Story = {
  render: () => (
    <DeploymentsScreen
      {...listProps({
        renderStartDialog: () => <StartDeploymentSheet {...START} />,
      })}
    />
  ),
};
