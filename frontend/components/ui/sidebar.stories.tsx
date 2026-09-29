import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { BotIcon, HomeIcon, PackageIcon, SettingsIcon } from "lucide-react";

import {
  Sidebar,
  SidebarContent,
  SidebarGroup,
  SidebarGroupLabel,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarProvider,
} from "@/components/ui/sidebar";

/** The rail primitive the shell's main navigation is built from (spec 44 §4.2). */
const meta: Meta = { title: "Atoms/Sidebar", parameters: { layout: "fullscreen" } };
export default meta;

const AREAS = [
  { label: "Home", icon: HomeIcon },
  { label: "Agents", icon: BotIcon },
  { label: "Apps", icon: PackageIcon },
  { label: "Admin", icon: SettingsIcon },
];

export const Default: StoryObj = {
  render: () => (
    <SidebarProvider>
      <Sidebar collapsible="icon">
        <SidebarContent>
          <SidebarGroup>
            <SidebarGroupLabel>Areas</SidebarGroupLabel>
            <SidebarMenu>
              {AREAS.map((a, i) => (
                <SidebarMenuItem key={a.label}>
                  <SidebarMenuButton isActive={i === 1}>
                    <a.icon />
                    <span>{a.label}</span>
                  </SidebarMenuButton>
                </SidebarMenuItem>
              ))}
            </SidebarMenu>
          </SidebarGroup>
        </SidebarContent>
      </Sidebar>
    </SidebarProvider>
  ),
};
