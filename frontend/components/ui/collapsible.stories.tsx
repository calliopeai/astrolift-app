import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { ChevronDownIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";

const meta: Meta = { title: "Atoms/Collapsible" };
export default meta;

export const Open: StoryObj = {
  render: () => (
    <Collapsible defaultOpen className="max-w-sm">
      <CollapsibleTrigger asChild>
        <Button variant="ghost">
          Advanced <ChevronDownIcon data-icon="inline-end" />
        </Button>
      </CollapsibleTrigger>
      <CollapsibleContent className="text-sm">
        Replicas, requests and autoscaling.
      </CollapsibleContent>
    </Collapsible>
  ),
};
