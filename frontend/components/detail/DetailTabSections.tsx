"use client";

import Link from "next/link";
import type * as React from "react";

import { cn } from "@/lib/utils";

export interface DetailTabSectionLink {
  id: string;
  label: string;
  href: string;
}

export interface DetailTabSectionsProps {
  /** The nav's accessible name. */
  ariaLabel: string;
  sections: DetailTabSectionLink[];
  /** The section on screen. */
  active: string;
  /** The active section's body; the route mounts only that one. */
  children: React.ReactNode;
}

/**
 * The sections inside a consolidated detail tab (spec 44 §5.2, §5.3): what
 * each former route became, as a list on the left that wraps above the body
 * below `md`. Links, so each section is in the URL (`?section=`) and survives
 * a reload; the route mounts and preloads only the active one (Leo's page
 * rule 2). A `danger-zone` section reads as destructive. Pure.
 */
export function DetailTabSections({
  ariaLabel,
  sections,
  active,
  children,
}: DetailTabSectionsProps) {
  return (
    <div className="flex min-w-0 flex-col gap-6 md:flex-row md:items-start">
      <nav aria-label={ariaLabel} className="min-w-0 md:w-48 md:shrink-0">
        <ul className="flex min-w-0 flex-wrap gap-1 md:flex-col md:flex-nowrap md:gap-0.5">
          {sections.map((s) => (
            <li key={s.id} className="min-w-0">
              <Link
                href={s.href}
                aria-current={s.id === active ? "page" : undefined}
                title={s.label}
                className={cn(
                  "block min-w-0 truncate rounded-sm px-2 py-1.5 text-sm transition-colors",
                  s.id === active
                    ? "bg-muted text-foreground font-medium"
                    : "text-muted-foreground hover:text-foreground",
                  s.id === "danger-zone" && "text-danger hover:text-danger"
                )}
              >
                {s.label}
              </Link>
            </li>
          ))}
        </ul>
      </nav>
      <div className="flex min-w-0 flex-1 flex-col gap-6">{children}</div>
    </div>
  );
}
