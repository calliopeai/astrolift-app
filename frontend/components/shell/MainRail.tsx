"use client";

import { ChevronDownIcon, ChevronsLeftIcon, ChevronsRightIcon } from "lucide-react";
import Link from "next/link";
import * as React from "react";

import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import type { NavArea, NavFunction } from "@/lib/shell/nav-model";
import { cn } from "@/lib/utils";

/**
 * The main rail: areas and their functions, on the left (spec 44 §4.1, §4.2).
 * Pure: the nav is already filtered to what the viewer may see, and the
 * active item and collapsed state are the caller's.
 *
 * Expanded, 220px: each area is a label with its functions under it, and
 * folds. Collapsed, 48px: the area icons; hovering or focusing one opens its
 * functions as a flyout.
 */
export interface MainRailProps {
  nav: NavArea[];
  active: { area: NavArea["key"]; fn: string } | null;
  collapsed: boolean;
  onCollapsedChange: (collapsed: boolean) => void;
  /** Above the areas: the brand. */
  header?: React.ReactNode;
  /** The brand's mark for the collapsed rail; nothing when absent. */
  headerCollapsed?: React.ReactNode;
  /** Below the areas: the user menu, theme and help. */
  footer?: React.ReactNode;
  className?: string;
}

export function MainRail({
  nav,
  active,
  collapsed,
  onCollapsedChange,
  header,
  headerCollapsed,
  footer,
  className,
}: MainRailProps) {
  return (
    <nav
      aria-label="Main"
      data-collapsed={collapsed || undefined}
      className={cn(
        "bg-sidebar text-sidebar-foreground border-sidebar-border flex h-full shrink-0 flex-col border-r transition-[width] duration-200",
        collapsed ? "w-12" : "w-[220px]",
        className
      )}
    >
      {/* Collapsed, the header shows its mark (or nothing): a name cut to
          one letter reads as a bug, not a logo. */}
      {collapsed
        ? headerCollapsed && <div className="flex justify-center py-3">{headerCollapsed}</div>
        : header && <div className="flex min-w-0 items-center px-3 py-3">{header}</div>}
      <div className="min-h-0 flex-1 overflow-y-auto px-2 py-1">
        {nav.map((area) =>
          collapsed ? (
            <CollapsedArea key={area.key} area={area} active={active} />
          ) : (
            <ExpandedArea key={area.key} area={area} active={active} />
          )
        )}
      </div>
      <div className="border-sidebar-border flex flex-col gap-1 border-t px-2 py-2">
        {!collapsed && footer}
        <button
          type="button"
          onClick={() => onCollapsedChange(!collapsed)}
          aria-label={collapsed ? "Expand the main rail" : "Collapse the main rail"}
          title={collapsed ? "Expand  [" : "Collapse  ["}
          className="text-muted-foreground hover:text-foreground hover:bg-sidebar-accent focus-visible:ring-ring flex h-8 items-center justify-center rounded-md focus-visible:ring-2 focus-visible:outline-none"
        >
          {collapsed ? (
            <ChevronsRightIcon className="size-4" />
          ) : (
            <ChevronsLeftIcon className="size-4" />
          )}
        </button>
      </div>
    </nav>
  );
}

function FunctionLink({
  fn,
  isActive,
  onNavigate,
}: {
  fn: NavFunction;
  isActive: boolean;
  onNavigate?: () => void;
}) {
  const Icon = fn.icon;
  return (
    <Link
      href={fn.href}
      onClick={onNavigate}
      aria-current={isActive ? "page" : undefined}
      data-onboarding-tour={fn.tourTarget}
      className={cn(
        "focus-visible:ring-ring flex h-8 min-w-0 items-center gap-2 rounded-md px-2 text-sm focus-visible:ring-2 focus-visible:outline-none",
        // The accent tint and a 2px accent edge: the rail's one lit row.
        isActive
          ? "text-foreground bg-[var(--brand-primary)]/15 font-medium shadow-[inset_2px_0_0_var(--brand-primary)]"
          : "text-sidebar-foreground/80 hover:bg-sidebar-accent/60 hover:text-sidebar-foreground"
      )}
    >
      <Icon className="size-4 shrink-0 opacity-80" aria-hidden />
      <span className="truncate">{fn.label}</span>
    </Link>
  );
}

function ExpandedArea({ area, active }: { area: NavArea; active: MainRailProps["active"] }) {
  const [open, setOpen] = React.useState(true);
  const isHome = area.key === "home";
  if (isHome) {
    const fn = area.groups[0]!.functions[0]!;
    return (
      <div className="mb-2">
        <FunctionLink fn={fn} isActive={active?.fn === fn.key} />
      </div>
    );
  }
  return (
    <div className="mb-2">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="text-muted-foreground hover:text-foreground text-2xs flex h-7 w-full items-center justify-between rounded-md px-2 font-semibold tracking-wider uppercase"
      >
        {area.label}
        <ChevronDownIcon
          className={cn("size-3 transition-transform", !open && "-rotate-90")}
          aria-hidden
        />
      </button>
      {open &&
        area.groups.map((group, i) => (
          <div key={group.label ?? i} className="flex flex-col gap-0.5">
            {group.label && (
              <div className="text-muted-foreground/80 text-2xs px-2 pt-2 pb-1">{group.label}</div>
            )}
            {group.functions.map((fn) => (
              <FunctionLink
                key={fn.key}
                fn={fn}
                isActive={active?.area === area.key && active.fn === fn.key}
              />
            ))}
          </div>
        ))}
    </div>
  );
}

function CollapsedArea({ area, active }: { area: NavArea; active: MainRailProps["active"] }) {
  const [open, setOpen] = React.useState(false);
  const Icon = area.icon;
  const isActive = active?.area === area.key;
  const single = area.groups.length === 1 && area.groups[0]!.functions.length === 1;
  const trigger = (
    <span
      className={cn(
        "flex size-8 items-center justify-center rounded-md",
        isActive
          ? "bg-sidebar-accent text-sidebar-accent-foreground"
          : "text-sidebar-foreground/80 hover:bg-sidebar-accent/60"
      )}
    >
      <Icon className="size-4" aria-hidden />
    </span>
  );
  if (single) {
    const fn = area.groups[0]!.functions[0]!;
    return (
      <Link
        href={fn.href}
        aria-label={area.label}
        title={area.label}
        aria-current={isActive ? "page" : undefined}
        className="mb-1 flex justify-center"
      >
        {trigger}
      </Link>
    );
  }
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        aria-label={area.label}
        className="mb-1 flex w-full justify-center focus-visible:outline-none"
        onMouseEnter={() => setOpen(true)}
      >
        {trigger}
      </PopoverTrigger>
      <PopoverContent
        side="right"
        align="start"
        className="w-56 p-2"
        onMouseLeave={() => setOpen(false)}
      >
        <div className="text-muted-foreground text-2xs px-2 pb-1 font-semibold tracking-wider uppercase">
          {area.label}
        </div>
        {area.groups.map((group, i) => (
          <div key={group.label ?? i} className="flex flex-col gap-0.5">
            {group.label && (
              <div className="text-muted-foreground/80 text-2xs px-2 pt-2 pb-1">{group.label}</div>
            )}
            {group.functions.map((fn) => (
              <FunctionLink
                key={fn.key}
                fn={fn}
                isActive={isActive && active?.fn === fn.key}
                onNavigate={() => setOpen(false)}
              />
            ))}
          </div>
        ))}
      </PopoverContent>
    </Popover>
  );
}
