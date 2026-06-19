"use client";

import { PlusIcon } from "lucide-react";
import Link from "next/link";

import { Button } from "@/components/ui/button";
import { SidebarGroup } from "@/components/ui/sidebar";
import { useModules } from "@/graphql/user/user.hooks";

/**
 * Prominent "Register App" CTA shown in the sidebar below the workspace
 * tree.
 *
 * Gated on the Apps module's `canCreate` (spec 36 §1.3) — the create
 * affordance is server-authoritative, so a viewer who can't create apps
 * doesn't see it. While `me.modules` is loading the button is hidden to
 * avoid a flash of an affordance the viewer may not have.
 */
export function RegisterAppButton() {
  const { canCreate, loading } = useModules();
  if (loading || !canCreate("apps")) return null;

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
