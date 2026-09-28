import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import {
  DETAIL,
  DETAIL_CRONJOB,
  DETAIL_EMPTY,
  DETAIL_LONG,
  MANIFEST,
  MANIFEST_EMPTY,
  SCALING,
  USAGE,
} from "./app-workloads.fixtures";
import { ManifestCardView } from "./ManifestCard";
import { ResourceUsageGaugesView } from "./ResourceUsageGauges";
import { ScalingCardView } from "./ScalingCard";
import { WorkloadDetailScreen } from "./WorkloadDetailScreen";

const meta: Meta<typeof WorkloadDetailScreen> = {
  title: "Screens/Apps/Workloads/WorkloadDetailScreen",
  component: WorkloadDetailScreen,
  parameters: { layout: "fullscreen" },
  args: {
    ...DETAIL,
    resourceUsage: <ResourceUsageGaugesView {...USAGE} />,
    scaling: <ScalingCardView {...SCALING} />,
    manifest: <ManifestCardView {...MANIFEST} />,
  },
};
export default meta;

type Story = StoryObj<typeof WorkloadDetailScreen>;

/** A deployment with init, primary and sidecar containers, a crash-looping pod, probes and volumes. */
export const Full: Story = {};

export const Loading: Story = { args: { workload: null, workloadLoading: true } };

/** Deployed nowhere yet: no pods, no containers, no probes, no volumes, no FQDN. */
export const Empty: Story = {
  args: {
    ...DETAIL_EMPTY,
    resourceUsage: <ResourceUsageGaugesView usage={null} loading={false} />,
    manifest: <ManifestCardView {...MANIFEST_EMPTY} />,
  },
};

/** Live sections still loading after the workload itself resolved. */
export const SectionsLoading: Story = {
  args: {
    ...DETAIL_EMPTY,
    podsLoading: true,
    bucketsLoading: true,
    containersLoading: true,
    resourceUsage: <ResourceUsageGaugesView usage={null} loading />,
    scaling: <ScalingCardView {...SCALING} status={null} loading />,
    manifest: <ManifestCardView manifest={null} loading />,
  },
};

/**
 * No workload with this slug, or no permission to see it. The screen has no
 * separate error state; a failed workload query lands here too.
 */
export const NotFound: Story = { args: { workload: null, workloadLoading: false } };

/** A cronjob: no scaling card, a recent-runs list instead. */
export const Cronjob: Story = { args: DETAIL_CRONJOB };

export const LongStrings: Story = { args: DETAIL_LONG };
