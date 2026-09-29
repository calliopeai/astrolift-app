"use client";

import { useTranslations } from "next-intl";
import type * as React from "react";

import { DetailTabSections } from "@/components/detail/DetailTabSections";

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
 * The sections inside a consolidated app tab (spec 44 §5.2, §5.3), on the
 * shared DetailTabSections: each section is in the URL (`?section=`,
 * `?view=`, `?kind=`) and survives a reload. Pure.
 */
export function AppTabSections({
  slug,
  basePath = "/apps",
  tab,
  active,
  children,
}: AppTabSectionsProps) {
  const t = useTranslations("apps.frame");
  const sections = (APP_TAB_SECTIONS[tab] ?? []).map((s) => ({
    id: s.id,
    label: t(`sections.${s.label}`),
    href: sectionHref(basePath, slug, tab, s),
  }));
  return (
    <DetailTabSections ariaLabel={t("sectionsAriaLabel")} sections={sections} active={active}>
      {children}
    </DetailTabSections>
  );
}
