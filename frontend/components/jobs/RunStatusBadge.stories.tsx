import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { RunStatusBadge } from "@/components/jobs/RunStatusBadge";

const meta: Meta = { title: "Primitives/Status/RunStatusBadge" };
export default meta;

export const All: StoryObj = {
  render: () => (
    <div className="flex flex-col gap-2">
      <RunStatusBadge status="running" />
      <RunStatusBadge status="in_progress" />
      <RunStatusBadge status="succeeded" exitCode={0} />
      <RunStatusBadge status="failed" exitCode={137} />
      <RunStatusBadge status="superseded" />
      <RunStatusBadge status="unknown" />
    </div>
  ),
};
