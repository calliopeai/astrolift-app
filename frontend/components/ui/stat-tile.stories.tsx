import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { RocketIcon } from "lucide-react";

import { StatTile } from "@/components/ui/stat-tile";

const meta: Meta<typeof StatTile> = { title: "Atoms/StatTile", component: StatTile };
export default meta;

export const States: StoryObj = {
  render: () => (
    <div className="grid max-w-3xl grid-cols-3 gap-4">
      <StatTile label="Deploys today" value={14} icon={RocketIcon} />
      <StatTile label="Success rate" value="97%" trend="+2%" />
      <StatTile label="Spend (30d)" value={null} loading />
    </div>
  ),
};
