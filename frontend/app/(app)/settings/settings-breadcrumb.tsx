"use client";

import { usePathname } from "next/navigation";
import { useTranslations } from "next-intl";

import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb";

import { findNavItemByPath, findSectionGroup } from "./settings-nav";

/**
 * Settings › Group › Page breadcrumb. Renders only on sub-routes —
 * on the /settings landing the page itself is the breadcrumb's only
 * segment, which adds noise without information, so we return null.
 *
 * Each segment is clickable (Settings → /settings, Group → /settings
 * landing with no anchor since groups don't have their own page).
 * Future: if a group ever gets its own index, swap the group segment
 * to a BreadcrumbLink with the group's href.
 */
export function SettingsBreadcrumb() {
  const pathname = usePathname();
  const tCrumb = useTranslations("settings.breadcrumb");
  const tNav = useTranslations("settings.nav");
  const tSections = useTranslations("settingsIndex.sections");

  if (pathname === "/settings" || pathname === "/settings/") {
    return null;
  }

  const item = findNavItemByPath(pathname);
  if (!item) {
    return (
      <Breadcrumb className="px-6 pt-4">
        <BreadcrumbList>
          <BreadcrumbItem>
            <BreadcrumbLink href="/settings">{tCrumb("root")}</BreadcrumbLink>
          </BreadcrumbItem>
        </BreadcrumbList>
      </Breadcrumb>
    );
  }

  const groupKey = findSectionGroup(item.key);
  const pageLabel = tSections(`${item.i18nKey}.title`);

  return (
    <Breadcrumb className="px-6 pt-4">
      <BreadcrumbList>
        <BreadcrumbItem>
          <BreadcrumbLink href="/settings">{tCrumb("root")}</BreadcrumbLink>
        </BreadcrumbItem>
        {groupKey && (
          <>
            <BreadcrumbSeparator />
            <BreadcrumbItem>
              <BreadcrumbLink href="/settings">{tNav(`groups.${groupKey}`)}</BreadcrumbLink>
            </BreadcrumbItem>
          </>
        )}
        <BreadcrumbSeparator />
        <BreadcrumbItem>
          <BreadcrumbPage>{pageLabel}</BreadcrumbPage>
        </BreadcrumbItem>
      </BreadcrumbList>
    </Breadcrumb>
  );
}
