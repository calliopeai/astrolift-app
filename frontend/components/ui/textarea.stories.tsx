import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { Textarea } from "@/components/ui/textarea";

const meta: Meta<typeof Textarea> = { title: "Atoms/Textarea", component: Textarea };
export default meta;

export const States: StoryObj = {
  render: () => (
    <div className="flex max-w-md flex-col gap-3">
      <Textarea placeholder="Why are you rejecting this deploy?" />
      <Textarea aria-invalid defaultValue="" placeholder="Reason required" />
      <Textarea disabled defaultValue="Disabled" />
    </div>
  ),
};
