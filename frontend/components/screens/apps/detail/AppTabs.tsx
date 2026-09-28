"use client";

import { useTranslations } from "next-intl";

import { DetailTabRow } from "@/components/DetailPageTabs";

import { appTabs } from "./app-tabs-model";

export {
  activeSection,
  APP_TAB_SECTIONS,
  APP_TABS,
  type AppTabKey,
  type AppTabSection,
  type AppTabSpec,
  appTabHref,
  appTabs,
  resolveAppTab,
  sectionHref,
} from "./app-tabs-model";

// The tab model lives in `app-tabs-model.ts` so server routes (redirects,
// section picks) can read it; this file is the client half.

export interface AppTabsViewProps {
  slug: string;
  /** `/apps` normally, `/agents` inside the agent shell (app chrome). */
  basePath: string;
  /** The current pathname; the source of truth for the active tab. */
  pathname: string;
  /**
   * Fallback for a path the model does not know: a tab key, or the name of a
   * route a tab absorbed (`"console"`, `"members"`). Otherwise inferred.
   */
  active?: string;
}

/**
 * The app's tab row on its own, for a page that draws its own header.
 * AppFrame carries the same row inside its ShellHeader.
 */
export function AppTabsView({ slug, basePath, pathname, active }: AppTabsViewProps) {
  const t = useTranslations("apps.tabs");
  return (
    <div className="-mx-6 min-w-0">
      <DetailTabRow
        tabs={appTabs(basePath, slug, pathname, (k) => t(k), active)}
        ariaLabel={t("ariaLabel")}
      />
    </div>
  );
}
