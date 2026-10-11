"use client";

import * as React from "react";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetClose } from "@/components/ui/sheet";

import { cn } from "@/lib/utils";

/**
 * Layout D (spec 44 §4.2): the main rail on the left, the content between,
 * the projects rail on the right. Pure: it lays the three out and owns only
 * the keyboard shortcuts, `[` for the main rail and `]` for projects, which
 * it hands to the caller's collapse state.
 */
export interface AppShellProps {
  mainRail: React.ReactNode;
  projectsRail: React.ReactNode;
  children: React.ReactNode;
  onToggleMain?: () => void;
  onToggleProjects?: () => void;
  /** Above the content, full width: incident banners. */
  banner?: React.ReactNode;
  className?: string;
}

function isTyping(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName);
}

export function AppShell({
  mainRail,
  projectsRail,
  children,
  onToggleMain,
  onToggleProjects,
  banner,
  className,
}: AppShellProps) {
  const t = useTranslations("responsiveShell");
  const [mobileRail, setMobileRail] = React.useState<"navigation" | "projects" | null>(null);
  React.useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.metaKey || e.ctrlKey || e.altKey || isTyping(e.target)) return;
      if (e.key === "[" && onToggleMain) {
        e.preventDefault();
        onToggleMain();
      } else if (e.key === "]" && onToggleProjects) {
        e.preventDefault();
        onToggleProjects();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onToggleMain, onToggleProjects]);

  return (
    <div
      className={cn(
        "bg-background text-foreground flex h-screen min-h-0 w-full flex-col",
        className
      )}
    >
      {banner}
      <div className="flex shrink-0 gap-2 border-b p-2 md:hidden">
        <Button variant="outline" onClick={() => setMobileRail("navigation")}>
          {t("navigation")}
        </Button>
        <Button variant="outline" onClick={() => setMobileRail("projects")}>
          {t("projects")}
        </Button>
      </div>
      <Sheet
        open={mobileRail !== null}
        onOpenChange={(open) => {
          if (!open) setMobileRail(null);
        }}
      >
        <SheetContent
          side={mobileRail === "projects" ? "right" : "left"}
          showCloseButton={false}
          className="gap-0"
        >
          <SheetHeader className="flex-row items-center justify-between">
            <SheetTitle>{t(mobileRail ?? "navigation")}</SheetTitle>
            <SheetClose asChild>
              <Button variant="outline">{t("close")}</Button>
            </SheetClose>
          </SheetHeader>
          <div
            className="min-h-0 flex-1 overflow-y-auto"
            onClick={(event) => {
              if ((event.target as HTMLElement).closest("a[href]")) setMobileRail(null);
            }}
          >
            {mobileRail === "projects" ? projectsRail : mainRail}
          </div>
        </SheetContent>
      </Sheet>
      <div className="flex min-h-0 flex-1">
        <div className="hidden shrink-0 md:block">{mainRail}</div>
        <main id="main-content" className="min-w-0 flex-1 overflow-y-auto px-3 py-5 md:px-6">
          {children}
        </main>
        <div className="hidden shrink-0 md:block">{projectsRail}</div>
      </div>
    </div>
  );
}
