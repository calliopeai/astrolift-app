import type { Decorator, Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";
import { expect, userEvent, within } from "storybook/test";

import { CLUSTERS_VIEW_KEY, ClustersList } from "./ClustersList";
import { CLUSTERS, LONG_CLUSTER, REGISTER, listProps } from "./fixtures";
import { RegisterClusterSheet } from "./RegisterClusterSheet";

// The card/list choice persists in localStorage; pin it per story so one
// story's mode never leaks into the next.
const withView: Decorator = (Story, { parameters }) => {
  try {
    localStorage.setItem(CLUSTERS_VIEW_KEY, parameters.view ?? "card");
  } catch {
    // storage unavailable: the view falls back to cards
  }
  return <Story />;
};

const meta: Meta = {
  title: "Screens/Clusters/ClustersList",
  parameters: { layout: "fullscreen" },
  decorators: [withView],
};
export default meta;

type Story = StoryObj;

export const Cards: Story = { render: () => <ClustersList {...listProps()} /> };

export const CardsLoading: Story = {
  render: () => <ClustersList {...listProps({ state: "loading", rows: [] })} />,
};

export const CardsEmpty: Story = {
  render: () => <ClustersList {...listProps({ state: "empty", rows: [], totalCount: 0 })} />,
};

export const CardsEmptyFiltered: Story = {
  render: () => (
    <ClustersList
      {...listProps({ state: "emptyFiltered", rows: [], isFiltered: true, search: "zzz" })}
    />
  ),
};

export const CardsError: Story = {
  render: () => (
    <ClustersList
      {...listProps({
        state: "error",
        rows: [],
        error: new globalThis.Error("upstream timed out"),
      })}
    />
  ),
};

export const CardsRefetching: Story = {
  render: () => <ClustersList {...listProps({ isStale: true })} />,
};

export const Table: Story = {
  parameters: { view: "list" },
  render: () => <ClustersList {...listProps()} />,
};

export const TableLoading: Story = {
  parameters: { view: "list" },
  render: () => <ClustersList {...listProps({ state: "loading", rows: [] })} />,
};

export const TableEmpty: Story = {
  parameters: { view: "list" },
  render: () => <ClustersList {...listProps({ state: "empty", rows: [], totalCount: 0 })} />,
};

export const TableError: Story = {
  parameters: { view: "list" },
  render: () => (
    <ClustersList
      {...listProps({
        state: "error",
        rows: [],
        error: new globalThis.Error("upstream timed out"),
      })}
    />
  ),
};

export const TablePaged: Story = {
  parameters: { view: "list" },
  render: () => (
    <ClustersList {...listProps({ totalCount: 60, hasNext: true, hasPrev: true, pageIndex: 1 })} />
  ),
};

export const LongStringsCards: Story = {
  render: () => <ClustersList {...listProps({ rows: [LONG_CLUSTER, ...CLUSTERS] })} />,
};

export const LongStringsTable: Story = {
  parameters: { view: "list" },
  render: () => <ClustersList {...listProps({ rows: [LONG_CLUSTER, ...CLUSTERS] })} />,
};

/** The register button opens the sheet the caller renders. */
export const OpensRegisterSheet: Story = {
  render: () => (
    <ClustersList
      {...listProps()}
      renderRegisterDialog={(p) => <RegisterClusterSheet {...REGISTER} {...p} />}
    />
  ),
  play: async ({ canvasElement }) => {
    await userEvent.click(within(canvasElement).getByRole("button", { name: /Register cluster/ }));
    await expect(
      await within(document.body).findByRole("heading", { name: "Register cluster" })
    ).toBeInTheDocument();
  },
};
