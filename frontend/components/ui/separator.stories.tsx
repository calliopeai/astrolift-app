import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { Separator } from "@/components/ui/separator";

const meta: Meta = { title: "Atoms/Separator" };
export default meta;

export const Orientations: StoryObj = {
  render: () => (
    <div className="flex max-w-md flex-col gap-4 text-sm">
      <span>Above</span>
      <Separator />
      <div className="flex h-5 items-center gap-3">
        <span>Left</span>
        <Separator orientation="vertical" />
        <span>Right</span>
      </div>
    </div>
  ),
};
