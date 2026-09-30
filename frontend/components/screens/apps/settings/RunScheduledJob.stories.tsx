import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { NextIntlClientProvider } from "next-intl";
import ja from "@/messages/ja.json";

import { CRON_JOBS, LONG, RUN_JOB } from "./app-settings-members.fixtures";
import { RunScheduledJobView } from "./RunScheduledJob";

const meta: Meta<typeof RunScheduledJobView> = {
  title: "Screens/Apps/Settings/RunScheduledJob",
  component: RunScheduledJobView,
  args: RUN_JOB,
};
export default meta;

type Story = StoryObj<typeof RunScheduledJobView>;

export const Full: Story = {};

/**
 * First load and "no cronjob workloads" both hide the card entirely, so the
 * Loading and Empty stories render nothing inside the frame.
 */
export const Loading: Story = { args: { cronJobs: [], workloadsLoading: true } };

export const Empty: Story = { args: { cronJobs: [] } };

/** No error state; a failed run is a toast. Shown: a run in flight. */
export const Running: Story = { args: { running: true } };

export const LongStrings: Story = {
  args: { cronJobs: [{ ...CRON_JOBS[0], slug: LONG, schedule: "0 2 * * *" }] },
};

export const JapaneseWidth768: Story = {
  render: (args) => (
    <NextIntlClientProvider locale="ja" messages={ja} timeZone="UTC">
      <div style={{ width: 768 }}>
        <RunScheduledJobView {...args} />
      </div>
    </NextIntlClientProvider>
  ),
};
