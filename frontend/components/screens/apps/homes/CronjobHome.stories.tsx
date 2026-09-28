import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import {
  CRON_WORKLOAD,
  CRONJOB,
  CRONJOB_EMPTY,
  CRONJOB_ERROR,
  CRONJOB_LOADING,
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
