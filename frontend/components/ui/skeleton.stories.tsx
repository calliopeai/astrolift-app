import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { Skeleton } from "@/components/ui/skeleton";

const meta: Meta = { title: "Atoms/Skeleton" };
export default meta;

/** Loading rows at the table's own height (spec 44 §5.1). */
export const TableRows: StoryObj = {
  render: () => (
    <div className="flex max-w-lg flex-col gap-2">
      {Array.from({ length: 5 }, (_, i) => (
        <Skeleton key={i} className="h-8 w-full" />
      ))}
    </div>
  ),
};
