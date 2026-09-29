import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { BotIcon, RocketIcon } from "lucide-react";

import { KpiTile } from "@/components/KpiTile";

const meta: Meta = { title: "Patterns/KpiTile" };
export default meta;

export const States: StoryObj = {
  render: () => (
    <div className="grid max-w-3xl grid-cols-3 gap-4">
      <KpiTile label="Deploys today" icon={RocketIcon} value={14} trend="+3" href="#" />
      <KpiTile label="Agent runs" icon={BotIcon} value={0} emptyCta="Run an agent" href="#" />
      <KpiTile label="Spend (30d)" icon={RocketIcon} value={null} loading />
    </div>
  ),
};
