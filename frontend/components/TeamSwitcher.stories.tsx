import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { Building2Icon } from "lucide-react";

import { TeamSwitcher } from "@/components/TeamSwitcher";
import { SidebarProvider } from "@/components/ui/sidebar";

const meta: Meta = { title: "Patterns/Shell/TeamSwitcher" };
export default meta;

export const Default: StoryObj = {
  render: () => (
    <SidebarProvider>
      <div className="w-64">
        <TeamSwitcher
          teams={[
            { name: "commerce", logo: <Building2Icon />, plan: "Enterprise" },
            { name: "data-platform", logo: <Building2Icon />, plan: "Enterprise" },
          ]}
        />
      </div>
    </SidebarProvider>
  ),
};
