"use client";

import Link from "next/link";

import { cn } from "@/lib/utils";

/**
 * The tab bars of every entity detail page (spec 35 §A.5, spec 44 §7).
 *
 * App, agent, cluster and workflow detail each drew their own copy of the same
 * link-nav strip. The route models stay with each entity (which routes exist,
 * which one owns the pathname); what they render is this, once, so a fix to
 * overflow, focus or the active mark lands on every detail page.
 */

export interface DetailTab {
  key: string;
  label: string;
  href: string;
  active: boolean;
}

/** Edge fade over a strip that scrolls sideways on narrow screens. */
export const EDGE_FADE =
  "[mask-image:linear-gradient(to_right,transparent_0,black_1.5rem,black_calc(100%-3rem),transparent_100%)]";

/** A row of underline tabs. The whole nav for a single-level entity. */
export function DetailTabRow({
  tabs,
  ariaLabel,
  className,
}: {
  tabs: DetailTab[];
  ariaLabel: string;
  className?: string;
}) {
  return (
    <nav
      aria-label={ariaLabel}
      className={cn(
        "border-border scrollbar-none flex min-w-0 gap-1 overflow-x-auto border-b px-6",
        EDGE_FADE,
        className
      )}
    >
      {tabs.map((tab) => (
        <Link
          key={tab.key}
          href={tab.href}
          aria-current={tab.active ? "page" : undefined}
          className={cn(
            "relative shrink-0 px-3 py-2.5 text-sm transition-colors",
            tab.active
              ? "text-foreground font-medium"
              : "text-muted-foreground hover:text-foreground"
          )}
        >
          {tab.label}
          {tab.active && (
            <span className="absolute inset-x-1 -bottom-px h-0.5 rounded-full bg-[var(--brand-primary)]" />
          )}
        </Link>
      ))}
    </nav>
  );
}

/**
 * A detail page's tabs: one row, named by function (spec 44 §5.2). Always
 * rendered when a page has tabs, even one, so the rhythm is the same on
 * every detail page.
 */
export function DetailPageTabs({ tabs, ariaLabel }: { tabs: DetailTab[]; ariaLabel: string }) {
  return (
    <div className="-mx-6 min-w-0">
      <DetailTabRow tabs={tabs} ariaLabel={ariaLabel} />
    </div>
  );
}
