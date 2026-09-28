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
const FADE =
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
        FADE,
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
 * BROCS pillars as a segmented mode switcher, above a row of sub-tabs.
 *
 * Contained chips read as "which layer am I in", categorically distinct from
 * the underline tabs below; two stacked underline rows were indistinguishable
 * at a glance. Mono uppercase because BROCS is the brand vocabulary, and each
 * initial carries the brand colour: the five of them spell BROCS. The initial
 * is split inside one element so the accessible name is still the whole word.
 */
export function DetailPillarBar({
  pillars,
  ariaLabel,
}: {
  pillars: DetailTab[];
  ariaLabel: string;
}) {
  return (
    <nav
      aria-label={ariaLabel}
      className={cn("scrollbar-none flex min-w-0 gap-1 overflow-x-auto px-6 pt-1 pb-2", FADE)}
    >
      <div className="bg-muted/40 border-border flex shrink-0 gap-0.5 rounded-sm border p-0.5">
        {pillars.map((pillar) => (
          <Link
            key={pillar.key}
            href={pillar.href}
            aria-current={pillar.active ? "page" : undefined}
            className={cn(
              "shrink-0 rounded-sm px-3.5 py-1.5 font-mono text-sm font-medium tracking-wider uppercase transition-colors",
              pillar.active
                ? "text-foreground bg-[var(--brand-primary)]/12 shadow-[inset_0_0_0_1px_var(--brand-primary)]"
                : "text-muted-foreground hover:text-foreground hover:bg-muted/60"
            )}
          >
            <span className="text-[var(--brand-primary)]">{pillar.label.charAt(0)}</span>
            {pillar.label.slice(1)}
          </Link>
        ))}
      </div>
    </nav>
  );
}

/**
 * A detail page's tabs: an optional pillar bar, then the tab row. Always
 * renders the row when given, even with one tab, so the rhythm stays stable
 * across pillars.
 */
export function DetailPageTabs({
  pillars,
  pillarsAriaLabel = "Pillars",
  tabs,
  tabsAriaLabel,
}: {
  pillars?: DetailTab[];
  pillarsAriaLabel?: string;
  tabs: DetailTab[];
  tabsAriaLabel: string;
}) {
  return (
    <div className="-mx-6 min-w-0">
      {pillars && <DetailPillarBar pillars={pillars} ariaLabel={pillarsAriaLabel} />}
      <DetailTabRow tabs={tabs} ariaLabel={tabsAriaLabel} />
    </div>
  );
}
