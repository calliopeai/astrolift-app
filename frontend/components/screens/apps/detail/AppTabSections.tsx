"use client";

import Link from "next/link";
import { useTranslations } from "next-intl";
import type * as React from "react";

import { cn } from "@/lib/utils";

import { APP_TAB_SECTIONS, type AppTabKey, sectionHref } from "./app-tabs-model";

export interface AppTabSectionsProps {
  slug: string;
  /** `/apps`. */
  basePath?: string;
  /** A tab that holds sections (Deployments, Workloads, Logs & metrics, Access, Settings). */
  tab: AppTabKey;
  /** The section on screen, from `activeSection`. */
  active: string;
  /** The section's body. */
  children: React.ReactNode;
}

/**
 * The sections inside a consolidated tab (spec 44 §5.2, §5.3): what each
 * former route became, as a list on the left that wraps above the body below
 * `md`. Links, so each section is in the URL (`?section=`, `?view=`,
 * `?kind=`) and survives a reload. Pure.
 */
export function AppTabSections({
  slug,
  basePath = "/apps",
  tab,
  active,
  children,
}: AppTabSectionsProps) {
  const t = useTranslations("apps.frame");
  const sections = APP_TAB_SECTIONS[tab] ?? [];
  return (
    <div className="flex min-w-0 flex-col gap-6 md:flex-row md:items-start">
      <nav aria-label={t("sectionsAriaLabel")} className="min-w-0 md:w-48 md:shrink-0">
        <ul className="flex min-w-0 flex-wrap gap-1 md:flex-col md:gap-0.5">
          {sections.map((s) => (
            <li key={s.id} className="min-w-0">
              <Link
                href={sectionHref(basePath, slug, tab, s)}
                aria-current={s.id === active ? "page" : undefined}
                className={cn(
                  "block min-w-0 truncate rounded-sm px-2 py-1.5 text-sm transition-colors",
                  s.id === active
                    ? "bg-muted text-foreground font-medium"
                    : "text-muted-foreground hover:text-foreground",
                  s.id === "danger-zone" && "text-danger hover:text-danger"
                )}
              >
                {t(`sections.${s.label}`)}
              </Link>
            </li>
          ))}
        </ul>
      </nav>
      <div className="flex min-w-0 flex-1 flex-col gap-6">{children}</div>
    </div>
  );
}
