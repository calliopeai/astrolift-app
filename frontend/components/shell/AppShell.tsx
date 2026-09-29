"use client";

import * as React from "react";

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
      <div className="flex min-h-0 flex-1">
        {mainRail}
        <main id="main-content" className="min-w-0 flex-1 overflow-y-auto px-6 py-5">
          {children}
        </main>
        {projectsRail}
      </div>
    </div>
  );
}
