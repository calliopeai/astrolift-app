import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

const meta: Meta = { title: "Atoms/Select" };
export default meta;

export const Default: StoryObj = {
  render: () => (
    <Select defaultValue="production">
      <SelectTrigger className="w-56" aria-label="Environment">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectGroup>
          <SelectLabel>Environment</SelectLabel>
          <SelectItem value="production">production</SelectItem>
          <SelectItem value="staging">staging</SelectItem>
          <SelectItem value="preview">preview-pr-142</SelectItem>
        </SelectGroup>
      </SelectContent>
    </Select>
  ),
};
