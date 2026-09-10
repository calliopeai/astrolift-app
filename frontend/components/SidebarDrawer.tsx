"use client";

import { ChevronRightIcon } from "lucide-react";
import * as React from "react";

import { useSidebar } from "@/components/ui/sidebar";
import { cn } from "@/lib/utils";

/**
 * One independently collapsible, independently scrolling section of the
 * sidebar rail (#1728).
 *
 * The rail carries two navigations with different jobs: a workspace tree
 * that grows with the estate and a fixed set of platform destinations.
 * Sharing one scroll container meant the first pushed the second off the
 * bottom of a real org's sidebar, and the only collapse control took both
 * away together.
 *
 * Each drawer owns its own scroll region, so neither can displace the
 * other, and its own open/closed state, persisted per browser -- sidebar
 * layout is a per-viewer convenience, not something to sync across an org.
 *
 * `grow` marks the drawer that absorbs leftover height (the tree). The
 * others keep their natural height and are never scrolled away.
 */
export interface SidebarDrawerProps {
  title: string;
  /** localStorage key holding this drawer's open/closed state. */
  storageKey: string;
  /** Take the leftover vertical space and scroll internally within it. */
  grow?: boolean;
  /**
   * Cap for a non-growing drawer, as a Tailwind max-height class. Its
   * content still scrolls inside the cap rather than pushing the growing
   * drawer down to nothing.
   */
  maxHeightClass?: string;
  children: React.ReactNode;
}

function loadOpen(key: string): boolean {
  try {
    return window.localStorage.getItem(key) !== "closed";
  } catch {
    return true;
  }
}

function saveOpen(key: string, open: boolean): void {
  try {
    window.localStorage.setItem(key, open ? "open" : "closed");
  } catch {
    /* private mode / storage disabled — the drawer just won't persist */
  }
}

export function SidebarDrawer({
  title,
  storageKey,
  grow = false,
  maxHeightClass,
  children,
}: SidebarDrawerProps) {
  const { state } = useSidebar();
  // Read on mount rather than during render: the server has no
  // localStorage, and an open drawer is the honest default for both.
  const [open, setOpen] = React.useState(true);
  React.useEffect(() => {
    setOpen(loadOpen(storageKey));
  }, [storageKey]);

  // In icon mode the rail is a column of icons with no headers to click,
  // so a closed drawer would hide its destinations with no way to get
  // them back. Icon mode shows everything; expanding restores the
  // operator's own choice.
  const iconMode = state === "collapsed";
  const expanded = open || iconMode;

  function toggle() {
    const next = !open;
    setOpen(next);
    saveOpen(storageKey, next);
  }

  return (
    <div
      data-sidebar-drawer={title.toLowerCase()}
      className={cn(
        "flex min-h-0 w-full min-w-0 flex-col",
        // Height sharing is an expanded-rail concern. In icon mode the
        // tree hides itself and the rail is a short column of icons, so
        // stretching one drawer would push the other's icons to the
        // bottom of an otherwise empty rail.
        grow && expanded && !iconMode ? "flex-1" : "shrink-0",
        !grow && expanded && !iconMode && maxHeightClass
      )}
    >
      <button
        type="button"
        onClick={toggle}
        aria-expanded={open}
        className={cn(
          "text-sidebar-foreground/70 hover:text-sidebar-foreground flex w-full shrink-0 items-center",
          "justify-between px-2 py-1.5 text-xs font-bold tracking-widest uppercase",
          "group-data-[collapsible=icon]:hidden"
        )}
      >
        <span>{title}</span>
        <ChevronRightIcon className={cn("size-3 transition-transform", open && "rotate-90")} />
      </button>
      {expanded ? (
        <div className="no-scrollbar min-h-0 flex-1 overflow-x-hidden overflow-y-auto">
          {children}
        </div>
      ) : null}
    </div>
  );
}
