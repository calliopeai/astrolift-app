import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

const meta: Meta = { title: "Atoms/Label" };
export default meta;

export const WithControl: StoryObj = {
  render: () => (
    <div className="flex max-w-sm flex-col gap-2">
      <Label htmlFor="app-name">App name</Label>
      <Input id="app-name" placeholder="checkout" />
    </div>
  ),
};
