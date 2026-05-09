"use client";

import { useTranslations } from "next-intl";

/**
 * Hidden skip link rendered at the top of the (app) layout. A keyboard
 * user can press Tab from the page load and jump directly to the main
 * content, bypassing the sidebar nav. WCAG 2.1 §2.4.1 (Bypass Blocks).
 *
 * Targets `#main-content` — that id lives on the <main> wrapper inside
 * SidebarInset (see app/(app)/layout.tsx).
 */
export function SkipToContent() {
  const t = useTranslations("a11y");
  return (
    <a
      href="#main-content"
      className="sr-only focus:not-sr-only focus:bg-primary focus:text-primary-foreground focus:fixed focus:left-4 focus:top-4 focus:z-[100] focus:rounded-md focus:px-3 focus:py-2 focus:shadow-md focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2"
    >
      {t("skipToContent")}
    </a>
  );
}
