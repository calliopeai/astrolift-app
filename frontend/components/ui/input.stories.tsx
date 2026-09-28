import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { Input } from "@/components/ui/input";

const meta: Meta<typeof Input> = { title: "Atoms/Input", component: Input };
export default meta;

export const States: StoryObj = {
  render: () => (
    <div className="flex max-w-sm flex-col gap-3">
      <Input placeholder="checkout" />
      <Input defaultValue="checkout" />
      <Input defaultValue="not a slug!" aria-invalid />
      <Input placeholder="Disabled" disabled />
      <Input type="password" defaultValue="hunter2" />
    </div>
  ),
};
