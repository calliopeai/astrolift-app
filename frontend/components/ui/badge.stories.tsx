import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { Badge } from "@/components/ui/badge";

const meta: Meta<typeof Badge> = { title: "Atoms/Badge", component: Badge };
export default meta;

const VARIANTS = ["default", "secondary", "outline", "ghost", "destructive", "link"] as const;

export const Variants: StoryObj = {
  render: () => (
    <div className="flex flex-wrap gap-2">
      {VARIANTS.map((v) => (
        <Badge key={v} variant={v}>
          {v}
        </Badge>
      ))}
    </div>
  ),
};
