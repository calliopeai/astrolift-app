import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { ClusterActivityBody, type ClusterActivityBodyProps } from "./ClusterActivityScreen";
import { ClusterTabFrame } from "./ClusterTabFrame";
import {
  AUDIT,
  AUDIT_LONG,
  CLUSTER,
  LONG_CLUSTER,
  QUERY_FAILED,
  QUERY_OK,
  WORKFLOWS,
  WORKFLOWS_LONG,
} from "./fixtures";

const meta: Meta = {
  title: "Screens/Clusters/Status/ClusterActivity",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

function Screen({
  cluster = CLUSTER,
  ...body
}: ClusterActivityBodyProps & { cluster?: typeof CLUSTER }) {
  return (
    <ClusterTabFrame slug={cluster.slug} cluster={cluster} loading={false} active="activity">
      <ClusterActivityBody {...body} />
    </ClusterTabFrame>
  );
}

export const Full: Story = { render: () => <Screen workflows={WORKFLOWS} lifecycle={AUDIT} /> };

export const UnknownStatusLongStrings: Story = {
  render: () => (
    <Screen
      workflows={{
        ...WORKFLOWS,
        runs: [
          {
            ...WORKFLOWS.runs[0],
            status: "LITERAL_FUTURE_WORKFLOW_STATUS_WITH_LONG_PROVIDER_DETAILS",
          },
        ],
      }}
      lifecycle={AUDIT}
    />
  ),
};

export const UnknownDuration: Story = {
  render: () => (
    <Screen
      workflows={{ ...WORKFLOWS, runs: [{ ...WORKFLOWS.runs[0], closedAt: "RAW_INVALID_TIME" }] }}
      lifecycle={AUDIT}
    />
  ),
};

export const CachedReadFailed: Story = {
  render: () => (
    <Screen
      workflows={{ ...WORKFLOWS, error: "RAW_TEMPORAL_READ_DIAGNOSTIC" }}
      lifecycle={{ ...AUDIT, error: "RAW_AUDIT_READ_DIAGNOSTIC" }}
    />
  ),
};

const MORE = { hasMore: true, loadingMore: false, onLoadMore: () => {} };

/** More behind each window: Load older at the end of each frame. */
export const HasOlder: Story = {
  render: () => (
    <Screen workflows={{ ...WORKFLOWS, more: MORE }} lifecycle={{ ...AUDIT, more: MORE }} />
  ),
};

export const Loading: Story = {
  render: () => (
    <Screen
      workflows={{ runs: [], loading: true, ...QUERY_OK }}
      lifecycle={{ entries: [], loading: true, ...QUERY_OK }}
    />
  ),
};

export const Empty: Story = {
  render: () => (
    <Screen
      workflows={{ runs: [], loading: false, ...QUERY_OK }}
      lifecycle={{ entries: [], loading: false, ...QUERY_OK }}
    />
  ),
};

/** Temporal and the audit log both failed: errors with a retry in each panel. */
export const LoadError: Story = {
  render: () => (
    <Screen
      workflows={{ runs: [], loading: false, ...QUERY_FAILED }}
      lifecycle={{ entries: [], loading: false, ...QUERY_FAILED }}
    />
  ),
};

/** Failed runs and failed mutations, with their first error shown. */
export const ErrorState: Story = {
  render: () => (
    <Screen
      workflows={{
        ...WORKFLOWS,
        runs: WORKFLOWS.runs.map((r) => ({ ...r, status: "FAILED" })),
      }}
      lifecycle={{
        ...AUDIT,
        entries: AUDIT.entries.map((e) => ({
          ...e,
          success: false,
          errors: e.errors.length ? e.errors : ["Permission denied: cluster.manage"],
        })),
      }}
    />
  ),
};

export const LongStrings: Story = {
  render: () => <Screen cluster={LONG_CLUSTER} workflows={WORKFLOWS_LONG} lifecycle={AUDIT_LONG} />,
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <Screen cluster={LONG_CLUSTER} workflows={WORKFLOWS_LONG} lifecycle={AUDIT_LONG} />
    </div>
  ),
};
