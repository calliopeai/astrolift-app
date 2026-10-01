"use client";

import { CheckIcon, ChevronDownIcon } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import * as React from "react";

import { type DetailTab, DetailTabRow } from "@/components/DetailPageTabs";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";

/**
 * Every page's header, three rows (spec 44 §4.4): a breadcrumb whose first
 * crumb switches functions (or projects), a title row, and at most one row of
 * tabs: views on a list, the entity's own tabs on a detail. Pure.
 */

export interface CrumbSwitchOption {
  label: string;
  href: string;
  active?: boolean;
}

export interface Crumb {
  label: string;
  /** Absent on the last crumb, the page itself. */
  href?: string;
  /** The sideways move: the area's functions, or the person's projects. */
  switcher?: CrumbSwitchOption[];
}

export interface ShellHeaderProps {
  crumbs: Crumb[];
  title: React.ReactNode;
  /** A status badge or dot, beside the title. */
  status?: React.ReactNode;
  /** One line of context: environment, region, model. */
  context?: React.ReactNode;
  /** The page's one primary action. */
  primaryAction?: React.ReactNode;
  /** The `⋯` menu. */
  menu?: React.ReactNode;
  tabs?: DetailTab[];
  tabsAriaLabel?: string;
  className?: string;
}

export function ShellHeader({
  crumbs,
  title,
  status,
  context,
  primaryAction,
  menu,
  tabs,
  tabsAriaLabel = "Page",
  className,
}: ShellHeaderProps) {
  const t = useTranslations("shared.shellHeader");
  return (
    <header className={cn("flex min-w-0 flex-col gap-3", className)}>
      <nav aria-label={t("breadcrumb")} className="min-w-0">
        <ol className="text-muted-foreground flex min-w-0 flex-wrap items-center gap-1.5 text-sm">
          {crumbs.map((crumb, i) => (
            <li key={`${crumb.label}-${i}`} className="flex min-w-0 items-center gap-1.5">
              {i > 0 && (
                <span aria-hidden className="text-muted-foreground/60">
                  ›
                </span>
              )}
              <CrumbItem crumb={crumb} isLast={i === crumbs.length - 1} />
            </li>
          ))}
        </ol>
      </nav>

      <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-2">
        <h1 className="font-head min-w-0 truncate text-xl font-semibold">{title}</h1>
        {status}
        {context && (
          <span className="text-muted-foreground min-w-0 truncate text-sm">{context}</span>
        )}
        {(primaryAction || menu) && (
          <div className="ml-auto flex shrink-0 items-center gap-2">
            {primaryAction}
            {menu}
          </div>
        )}
      </div>

      {tabs && tabs.length > 0 && (
        <div className="-mx-6 min-w-0">
          <DetailTabRow tabs={tabs} ariaLabel={tabsAriaLabel} />
        </div>
      )}
    </header>
  );
}

function CrumbItem({ crumb, isLast }: { crumb: Crumb; isLast: boolean }) {
  const t = useTranslations("shared.shellHeader");
  if (crumb.switcher && crumb.switcher.length > 0) {
    return (
      <DropdownMenu>
        <DropdownMenuTrigger
          className="hover:text-foreground focus-visible:ring-ring inline-flex min-w-0 items-center gap-1 rounded-sm focus-visible:ring-2 focus-visible:outline-none"
          aria-label={t("switch", { label: crumb.label })}
        >
          <span className="truncate">{crumb.label}</span>
          <ChevronDownIcon className="size-3.5 shrink-0" aria-hidden />
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="min-w-48">
          {crumb.switcher.map((o) => (
            <DropdownMenuItem key={o.href} asChild>
              <Link
                href={o.href}
                aria-current={o.active ? "page" : undefined}
                className="flex items-center gap-2"
              >
                <CheckIcon className={cn("size-3.5", !o.active && "invisible")} aria-hidden />
                {o.label}
              </Link>
            </DropdownMenuItem>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>
    );
  }
  if (isLast || !crumb.href) {
    return (
      <span aria-current="page" className="text-foreground min-w-0 truncate">
        {crumb.label}
      </span>
    );
  }
  return (
    <Link href={crumb.href} className="hover:text-foreground min-w-0 truncate">
      {crumb.label}
    </Link>
  );
}
