"use client";

import * as React from "react";

import { AstroliftNav } from "@/components/AstroliftNav";
import { LanguageSwitcher } from "@/components/LanguageSwitcher";
import { NavTree } from "@/components/NavTree";
import { NavUser } from "@/components/NavUser";
import { OrgSwitcher } from "@/components/OrgSwitcher";
import { ThemeToggle } from "@/components/ThemeToggle";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarHeader,
  SidebarRail,
  SidebarSeparator,
} from "@/components/ui/sidebar";
import type { CurrentUser } from "@/graphql/user/user.types";

type AppSidebarProps = React.ComponentProps<typeof Sidebar> & {
  ssrUser: CurrentUser | null;
};

export function AppSidebar({ ssrUser, ...props }: AppSidebarProps) {
  return (
    <Sidebar collapsible="icon" {...props}>
      <SidebarHeader>
        <OrgSwitcher />
      </SidebarHeader>
      <SidebarContent>
        {/* Tenant hierarchy first so operators land on their work
            without scrolling past platform-wide tabs. The flat
            AstroliftNav stays below for cross-cutting sections
            (Operations, Infrastructure, Administration, Account). */}
        <NavTree />
        <SidebarSeparator />
        <AstroliftNav />
      </SidebarContent>
      <SidebarFooter>
        <LanguageSwitcher />
        <ThemeToggle />
        <SidebarSeparator />
        <NavUser ssrUser={ssrUser} />
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  );
}
