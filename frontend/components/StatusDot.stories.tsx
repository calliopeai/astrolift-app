import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { StatusDot } from "@/components/StatusDot";

/** Status never follows the accent (lib/status-tones). */
const meta: Meta = { title: "Primitives/Status/StatusDot" };
export default meta;

export const All: StoryObj = {
  render: () => (
    <div className="flex items-center gap-4 text-sm">
      {(["ok", "warn", "error", "pending", "muted"] as const).map((s) => (
        <span key={s} className="inline-flex items-center gap-2">
          <StatusDot status={s} /> {s}
        </span>
      ))}
    </div>
  ),
};
