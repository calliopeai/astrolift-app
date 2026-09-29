import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { MoreHorizontalIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

/** The `⋯` row and page menu (spec 44 §4.4). */
const meta: Meta = { title: "Atoms/DropdownMenu" };
export default meta;

export const Open: StoryObj = {
  render: () => (
    <div className="p-16">
      <DropdownMenu defaultOpen>
        <DropdownMenuTrigger asChild>
          <Button size="icon" variant="ghost" aria-label="More">
            <MoreHorizontalIcon />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent>
          <DropdownMenuLabel>checkout</DropdownMenuLabel>
          <DropdownMenuItem>Redeploy</DropdownMenuItem>
          <DropdownMenuItem>View logs</DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem variant="destructive">Delete app</DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  ),
};
