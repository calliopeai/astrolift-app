import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { CronSchedulePreview } from "@/components/jobs/CronSchedulePreview";

const meta: Meta = { title: "Patterns/Jobs/CronSchedulePreview" };
export default meta;
export const Schedules: StoryObj = {
  render: () => (
    <div className="flex flex-col gap-3">
      <CronSchedulePreview schedule="0 3 * * *" />
      <CronSchedulePreview schedule="*/15 * * * 1-5" />
      <CronSchedulePreview schedule="not a schedule" />
    </div>
  ),
};
