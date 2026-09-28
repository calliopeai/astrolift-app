"use client";

import { QuickLinksGrid as QuickLinksGridView } from "@/components/screens/apps/detail/QuickLinksGrid";
import { useQuickLinks } from "@/components/screens/apps/detail/use-quick-links";

import { appPath, useAppChrome } from "./app-chrome-context";

/** The overview's quick-link grid; its deployment count query runs here. */
export function QuickLinksGrid({ appSlug }: { appSlug: string }) {
  const chrome = useAppChrome();
  return <QuickLinksGridView {...useQuickLinks(appSlug)} appHref={appPath(chrome, appSlug)} />;
}
