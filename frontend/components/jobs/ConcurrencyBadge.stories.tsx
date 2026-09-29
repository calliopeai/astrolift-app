import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { ConcurrencyBadge } from "@/components/jobs/ConcurrencyBadge";

const meta: Meta = { title: "Patterns/Jobs/ConcurrencyBadge" };
export default meta;
export const All: StoryObj = {
  render: () => (
    <div className="flex gap-2">
      {["forbid", "queue", "replace"].map((p) => (
        <ConcurrencyBadge key={p} policy={p} />
      ))}
    </div>
  ),
};
