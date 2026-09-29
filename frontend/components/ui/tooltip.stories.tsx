import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

const meta: Meta = { title: "Atoms/Tooltip" };
export default meta;

export const Open: StoryObj = {
  render: () => (
    <div className="p-16">
      <Tooltip defaultOpen>
        <TooltipTrigger asChild>
          <Button variant="outline">Hover me</Button>
        </TooltipTrigger>
        <TooltipContent>Deployed 2 minutes ago by leo@example.com</TooltipContent>
      </Tooltip>
    </div>
  ),
};
