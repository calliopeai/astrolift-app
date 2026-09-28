import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import {
  APPS,
  appsListProps,
  LONG_APP,
  PUSH_SECRETS,
  SELECTED,
} from "../new/apps-list-wizard-shell.fixtures";

import { AppsListScreen, PushSecretsDialog } from "./AppsListScreen";

const meta: Meta = {
  title: "Screens/Apps/List/AppsListScreen",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

/** Card view, one app pinned to the top. */
export const Full: Story = {
  render: () => <AppsListScreen {...appsListProps(APPS)} />,
};

export const ListView: Story = {
  render: () => <AppsListScreen {...appsListProps(APPS, { viewMode: "list" })} />,
};

export const Loading: Story = {
  render: () => <AppsListScreen {...appsListProps([], {}, { state: "loading" })} />,
};

export const ListLoading: Story = {
  render: () => (
    <AppsListScreen {...appsListProps([], { viewMode: "list" }, { state: "loading" })} />
  ),
};

/** No apps registered yet. */
export const Empty: Story = {
  render: () => <AppsListScreen {...appsListProps([])} />,
};

/** A status pill narrowed the walk to nothing. */
export const NoMatch: Story = {
  render: () => (
    <AppsListScreen
      {...appsListProps([], { pill: "degraded", narrowed: true, hasActiveFilters: true })}
    />
  ),
};

/** The search box narrowed the walk to nothing. */
export const EmptySearch: Story = {
  render: () => (
    <AppsListScreen
      {...appsListProps(
        [],
        { hasActiveFilters: true },
        { state: "emptyFiltered", search: "nope", isFiltered: true }
      )}
    />
  ),
};

export const LoadError: Story = {
  render: () => (
    <AppsListScreen
      {...appsListProps(
        [],
        {},
        { state: "error", error: new Error("Network error: failed to fetch") as never }
      )}
    />
  ),
};

/** Two apps selected: the bulk bar is up. */
export const Selected: Story = {
  render: () => <AppsListScreen {...appsListProps(APPS, { selection: SELECTED })} />,
};

/** Refetch in flight: the cards stay and fade. */
export const Stale: Story = {
  render: () => <AppsListScreen {...appsListProps(APPS, {}, { isStale: true })} />,
};

export const LongStrings: Story = {
  render: () => <AppsListScreen {...appsListProps([LONG_APP, ...APPS])} />,
};

export const LongStringsList: Story = {
  render: () => <AppsListScreen {...appsListProps([LONG_APP, ...APPS], { viewMode: "list" })} />,
};

export const PushSecrets: Story = {
  render: () => <PushSecretsDialog {...PUSH_SECRETS} />,
};

export const PushSecretsBusy: Story = {
  render: () => <PushSecretsDialog {...PUSH_SECRETS} busy />,
};
