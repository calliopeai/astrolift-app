import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { Button } from "@/components/ui/button";
import {
  Popover,
  PopoverContent,
  PopoverDescription,
  PopoverHeader,
  PopoverTitle,
  PopoverTrigger,
} from "@/components/ui/popover";

const meta: Meta = { title: "Atoms/Popover" };
export default meta;

export const Open: StoryObj = {
  render: () => (
    <div className="p-16">
      <Popover defaultOpen>
        <PopoverTrigger asChild>
          <Button variant="outline">Details</Button>
        </PopoverTrigger>
        <PopoverContent>
          <PopoverHeader>
            <PopoverTitle>Image</PopoverTitle>
            <PopoverDescription className="font-mono [overflow-wrap:anywhere]">
              registry.example.com/checkout@sha256:4f1c9e0a4f1c9e0a4f1c9e0a4f1c9e0a
            </PopoverDescription>
          </PopoverHeader>
        </PopoverContent>
      </Popover>
    </div>
  ),
};
