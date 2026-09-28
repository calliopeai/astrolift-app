import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { SidebarDrawer } from "@/components/SidebarDrawer";
import { SidebarProvider } from "@/components/ui/sidebar";

const meta: Meta = { title: "Patterns/Shell/SidebarDrawer" };
export default meta;

export const Default: StoryObj = {
  render: () => (
    <SidebarProvider>
      <div className="w-64">
        <SidebarDrawer title="Workspace" storageKey="story.drawer">
          <ul className="px-2 text-sm">
            <li>storefront</li>
            <li>data-platform</li>
          </ul>
        </SidebarDrawer>
      </div>
    </SidebarProvider>
  ),
};
