import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import {
  CRON_WORKLOAD,
  CRONJOB,
  CRONJOB_EMPTY,
  CRONJOB_ERROR,
  CRONJOB_LOADING,
  CRONJOB_LONG_HISTORY,
  LONG,
} from "./app-homes.fixtures";
import { CronjobHomeScreen } from "./CronjobHome";

const meta: Meta = {
  title: "Screens/Apps/Homes/CronjobHome",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

export const Full: Story = { render: () => <CronjobHomeScreen {...CRONJOB} /> };

export const Loading: Story = { render: () => <CronjobHomeScreen {...CRONJOB_LOADING} /> };

export const Empty: Story = { render: () => <CronjobHomeScreen {...CRONJOB_EMPTY} /> };

export const LoadError: Story = { render: () => <CronjobHomeScreen {...CRONJOB_ERROR} /> };

export const NoSchedule: Story = {
  render: () => (
    <CronjobHomeScreen
      {...CRONJOB_EMPTY}
      workload={{ ...CRON_WORKLOAD, schedule: "", concurrencyPolicy: "" }}
    />
  ),
};

export const LongStrings: Story = {
  render: () => (
    <CronjobHomeScreen
      {...CRONJOB}
      name={LONG}
      workload={{
        ...CRON_WORKLOAD,
        schedule: "0 0,6,12,18 1-7,15-21 1,4,7,10 1-5",
        concurrencyPolicy: LONG,
      }}
    />
  ),
};

/** The newest run failed: its log tail leads the first panel. */
export const LastRunFailed: Story = {
  render: () => (
    <CronjobHomeScreen
      {...CRONJOB}
      runs={[
        {
          ...CRONJOB.runs[0]!,
          status: "failed",
          exitCode: 137,
          logExcerpt: `pulling ${LONG}\nOOMKilled: container exceeded its 512Mi memory limit`,
        },
        ...CRONJOB.runs.slice(1),
      ]}
    />
  ),
};

/** Forty runs with older pages on the cursor: the feed scrolls in its own frame. */
export const LongHistory: Story = {
  render: () => <CronjobHomeScreen {...CRONJOB_LONG_HISTORY} />,
};

/** Older runs failed to load: the loaded ones stay, the error sits at the end. */
export const OlderPageError: Story = {
  render: () => (
    <CronjobHomeScreen {...CRONJOB_LONG_HISTORY} error="Network error: upstream timed out" />
  ),
};

export const At768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <CronjobHomeScreen {...CRONJOB} name={LONG} />
    </div>
  ),
};
