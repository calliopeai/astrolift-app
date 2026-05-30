"use client";

import { PlusIcon } from "lucide-react";
import Link from "next/link";

import { Button } from "@/components/ui/button";
import { SidebarGroup } from "@/components/ui/sidebar";

/**
 * Prominent "Register App" CTA shown in the sidebar below the workspace
 * tree. Visible to both admin and end-user audiences — it is the primary
 * onboarding action for any authenticated user.
 */
export function RegisterAppButton() {
  return (
    <SidebarGroup className="pb-2 pt-1">
      <Button
        asChild
        size="sm"
        className="w-full gap-1.5 font-medium"
      >
        <Link href="/apps/new">
          <PlusIcon className="size-3.5" />
          Register App
        </Link>
      </Button>
    </SidebarGroup>
  );
}
