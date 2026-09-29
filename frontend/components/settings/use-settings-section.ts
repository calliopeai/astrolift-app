"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import * as React from "react";

/**
 * Which section a single-section SettingsPage shows (spec 44 §5.3): the one
 * named by `?section=` in the URL, so a section can be linked and survives a
 * reload. The page falls back to its first section for an absent or unknown id.
 */
export interface SectionSelection {
  /** The id from the URL, as given; the page validates it. */
  active: string | null;
  /** The nav's link for a section. */
  href: (id: string) => string;
  /** The narrow-screen select's change. */
  select: (id: string) => void;
}

export const SECTION_PARAM = "section";

/** `?section=` beside whatever else is in the query. */
export function sectionHref(pathname: string, query: string, id: string): string {
  const p = new URLSearchParams(query);
  p.set(SECTION_PARAM, id);
  return `${pathname}?${p.toString()}`;
}

/** Section in the URL. Routed settings tabs use this. */
export function useSettingsSection(): SectionSelection {
  const params = useSearchParams();
  const pathname = usePathname() ?? "";
  const router = useRouter();
  const query = params?.toString() ?? "";
  const href = (id: string) => sectionHref(pathname, query, id);
  return {
    active: params?.get(SECTION_PARAM) ?? null,
    href,
    select: (id) => router.replace(href(id), { scroll: false }),
  };
}

/** Section in memory: stories. */
export function useLocalSettingsSection(initial: string | null = null): SectionSelection {
  const [active, setActive] = React.useState(initial);
  return { active, href: (id) => `?${SECTION_PARAM}=${encodeURIComponent(id)}`, select: setActive };
}
