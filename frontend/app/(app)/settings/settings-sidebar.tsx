"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useTranslations } from "next-intl";
import * as React from "react";

import { cn } from "@/lib/utils";

import { SETTINGS_NAV } from "./settings-nav";

interface SettingsSidebarProps {
  /** Optional callback fired when an item is selected, used by the
   *  mobile sheet to dismiss itself on navigation. */
  onNavigate?: () => void;
  /** Extra classes applied to the outer `<nav>`. */
  className?: string;
}

/**
 * Persistent left rail listing every settings sub-route grouped by
 * section. Shared between the desktop sticky aside and the mobile
 * sheet — the only difference is the wrapper layout, not the link
 * markup itself.
 */
export function SettingsSidebar({ onNavigate, className }: SettingsSidebarProps) {
  const pathname = usePathname();
  const tNav = useTranslations("settings.nav");
  const tSections = useTranslations("settingsIndex.sections");

  return (
    <nav aria-label={tNav("ariaLabel")} className={cn("flex flex-col gap-5", className)}>
      {SETTINGS_NAV.map((group) => (
        <NavGroup key={group.key} label={tNav(`groups.${group.key}`)}>
          {group.items.map(({ key, href, icon: Icon, i18nKey, external }) => {
            const active = !external && (pathname === href || pathname.startsWith(`${href}/`));
            return (
              <NavItem
                key={key}
                href={href}
                icon={<Icon className="size-4 shrink-0" />}
                label={tSections(`${i18nKey}.title`)}
                active={active}
                external={external}
                onNavigate={onNavigate}
              />
            );
          })}
        </NavGroup>
      ))}
    </nav>
  );
}

interface NavGroupProps {
  label: string;
  children: React.ReactNode;
}

function NavGroup({ label, children }: NavGroupProps) {
  return (
    <div className="flex flex-col gap-1">
      <p className="text-muted-foreground mb-1 px-2 text-xs font-semibold tracking-wider uppercase">
        {label}
      </p>
      {children}
    </div>
  );
}

interface NavItemProps {
  href: string;
  label: string;
  icon: React.ReactNode;
  active: boolean;
  external?: boolean;
  onNavigate?: () => void;
}

function NavItem({ href, label, icon, active, external, onNavigate }: NavItemProps) {
  return (
    <Link
      href={href}
      aria-current={active ? "page" : undefined}
      onClick={onNavigate}
      className={cn(
        "flex items-center gap-2.5 rounded-md px-2 py-2 text-sm transition-colors",
        active
          ? "bg-accent text-accent-foreground font-medium"
          : "text-muted-foreground hover:bg-accent hover:text-accent-foreground"
      )}
    >
      {icon}
      <span className="flex-1 truncate">{label}</span>
      {external && (
        <span aria-hidden="true" className="text-muted-foreground text-xs">
          ↗
        </span>
      )}
    </Link>
  );
}
