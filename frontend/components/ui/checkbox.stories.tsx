import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";

const meta: Meta = { title: "Atoms/Checkbox" };
export default meta;

export const States: StoryObj = {
  render: () => (
    <div className="flex flex-col gap-3">
      {[
        { id: "a", label: "Unchecked" },
        { id: "b", label: "Checked", checked: true },
        { id: "c", label: "Disabled", disabled: true },
      ].map((c) => (
        <div key={c.id} className="flex items-center gap-2">
          <Checkbox id={c.id} defaultChecked={c.checked} disabled={c.disabled} />
          <Label htmlFor={c.id}>{c.label}</Label>
        </div>
      ))}
    </div>
  ),
};
