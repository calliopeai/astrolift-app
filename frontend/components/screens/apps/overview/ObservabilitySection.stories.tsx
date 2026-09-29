import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import {
  OBSERVABILITY,
  OBSERVABILITY_EMPTY,
  OBSERVABILITY_FAILING,
  OBSERVABILITY_LOADING,
  OBSERVABILITY_LONG,
} from "./app-ci-observability-section.fixtures";
import { ObservabilitySectionView } from "./ObservabilitySection";

const meta: Meta = { title: "Screens/Apps/Overview/ObservabilitySection" };
export default meta;

type Story = StoryObj;

export const Full: Story = {
  render: () => <ObservabilitySectionView {...OBSERVABILITY} />,
};

export const Loading: Story = {
  render: () => <ObservabilitySectionView {...OBSERVABILITY_LOADING} />,
};

/** No deploys in the window, no unresolved alerts. */
export const Empty: Story = {
  render: () => <ObservabilitySectionView {...OBSERVABILITY_EMPTY} />,
};

/**
 * No error state of its own (a failed query renders as empty). The
 * closest: failing rollouts and critical alerts.
 */
export const FailuresAndCritical: Story = {
  render: () => <ObservabilitySectionView {...OBSERVABILITY_FAILING} />,
};

/** Overlong slug (in the links) and four-digit counts. */
export const LongStrings: Story = {
  render: () => <ObservabilitySectionView {...OBSERVABILITY_LONG} />,
};

export const At768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <ObservabilitySectionView {...OBSERVABILITY_LONG} />
    </div>
  ),
};
