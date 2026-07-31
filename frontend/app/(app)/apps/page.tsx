import { AppsClient } from "./apps-client";

export const metadata = { title: "Apps · Astrolift" };

/**
 * No PreloadQuery here on purpose (#1232). This page used to warm
 * ``LIST_APPS`` — the unbounded list — while the client walks
 * ``LIST_APPS_PAGE``, so the SSR round trip pulled the whole registry
 * into a cache entry nothing ever read. The paged query's first-page
 * variables depend on the URL's filter state, the operator's stored
 * sort, and the page size the controller restores, so there is no
 * single set of variables a server preload could match either.
 */
export default function AppsPage() {
  return <AppsClient />;
}
