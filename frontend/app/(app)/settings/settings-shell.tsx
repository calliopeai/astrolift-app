"use client";

import { MenuIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";

import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";

import { SettingsBreadcrumb } from "./settings-breadcrumb";
import { SettingsSidebar } from "./settings-sidebar";

/**
 * Two-column shell used by the /settings route subtree:
 *
 *   md+:  [ sticky sidebar | breadcrumb + content ]
 *   <md:  [ "Sections" sheet trigger ] above each page
 *
 * The sidebar is rendered as a server-friendly `<aside>` on desktop
 * but the inner `<SettingsSidebar>` itself is a client component
 * because it reads `usePathname()` for the active-route highlight.
 *
 * The mobile sheet auto-dismisses on navigation so the operator
 * never has to manually close it after picking a destination.
 */
export function SettingsShell({ children }: { children: React.ReactNode }) {
  const t = useTranslations("settings.nav");
  const [open, setOpen] = React.useState(false);

  return (
    <div className="flex flex-1">
      <aside className="bg-background hidden w-56 shrink-0 border-r md:flex md:flex-col">
        <div className="sticky top-0 max-h-[calc(100vh-4rem)] overflow-y-auto p-4">
          <SettingsSidebar />
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex items-center gap-2 border-b px-4 py-2 md:hidden">
          <Sheet open={open} onOpenChange={setOpen}>
            <SheetTrigger asChild>
              <Button variant="ghost" size="sm" className="gap-2" aria-label={t("openMobileNav")}>
                <MenuIcon className="size-4" />
                <span>{t("openMobileNavLabel")}</span>
              </Button>
            </SheetTrigger>
            <SheetContent side="top" className="max-h-[80vh] overflow-y-auto">
              <SheetHeader>
                <SheetTitle>{t("mobileTitle")}</SheetTitle>
                <SheetDescription>{t("mobileDescription")}</SheetDescription>
              </SheetHeader>
              <div className="px-4 pb-4">
                <SettingsSidebar onNavigate={() => setOpen(false)} />
              </div>
            </SheetContent>
          </Sheet>
        </div>

        <SettingsBreadcrumb />

        <div className="flex flex-1 flex-col">{children}</div>
      </div>
    </div>
  );
}
