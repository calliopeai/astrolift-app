import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { ThemeToggle } from "@/components/ThemeToggle";
import { SidebarProvider } from "@/components/ui/sidebar";

const meta: Meta = { title: "Patterns/Shell/ThemeToggle" };
export default meta;

export const Select: StoryObj = { render: () => <ThemeToggle variant="select" /> };
export const Sidebar: StoryObj = {
  render: () => (
    <SidebarProvider>
      <ThemeToggle variant="sidebar" />
    </SidebarProvider>
  ),
};
