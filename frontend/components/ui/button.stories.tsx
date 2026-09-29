import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { PlusIcon, RocketIcon, TrashIcon } from "lucide-react";

import { Button } from "@/components/ui/button";

const meta: Meta<typeof Button> = { title: "Atoms/Button", component: Button };
export default meta;

const VARIANTS = ["default", "secondary", "outline", "ghost", "destructive", "link"] as const;
const SIZES = ["xs", "sm", "default", "lg"] as const;

export const Variants: StoryObj = {
  render: () => (
    <div className="flex flex-col gap-4">
      {VARIANTS.map((variant) => (
        <div key={variant} className="flex flex-wrap items-center gap-3">
          <span className="text-muted-foreground w-24 font-mono text-xs">{variant}</span>
          {SIZES.map((size) => (
            <Button key={size} variant={variant} size={size}>
              Deploy
            </Button>
          ))}
          <Button variant={variant} disabled>
            Disabled
          </Button>
        </div>
      ))}
    </div>
  ),
};

export const WithIcons: StoryObj = {
  render: () => (
    <div className="flex flex-wrap items-center gap-3">
      <Button>
        <RocketIcon data-icon="inline-start" /> Deploy
      </Button>
      <Button variant="outline">
        <PlusIcon data-icon="inline-start" /> New app
      </Button>
      <Button variant="destructive">
        <TrashIcon data-icon="inline-start" /> Delete
      </Button>
      <Button size="icon" aria-label="Add">
        <PlusIcon />
      </Button>
      <Button size="icon-sm" variant="ghost" aria-label="Delete">
        <TrashIcon />
      </Button>
    </div>
  ),
};

export const LongLabel: StoryObj = {
  render: () => (
    <div className="w-64">
      <Button className="max-w-full">
        Deploy 1112015d8a9b7c6e5f4d3c2b1a0f9e8d7c6b5a49 to production
      </Button>
    </div>
  ),
};
