"use client";

import * as React from "react";

export type ProviderTab = "cloud" | "source" | "identity";

const VALID_TABS = new Set<ProviderTab>(["cloud", "source", "identity"]);

function tabFromHash(): ProviderTab {
  if (typeof window === "undefined") return "cloud";
  const h = window.location.hash.replace(/^#/, "") as ProviderTab;
  return VALID_TABS.has(h) ? h : "cloud";
}

/**
 * The Providers page's active tab, mirrored to the URL hash so the folded
 * /settings/source-providers and /settings/identity-provider routes can
 * redirect straight to the right section.
 */
export function useProvidersTab() {
  const [tab, setTab] = React.useState<ProviderTab>("cloud");

  // Sync from the hash on mount and on back/forward navigation so a
  // redirect to /providers#source lands on the Source tab.
  React.useEffect(() => {
    setTab(tabFromHash());
    const onHashChange = () => setTab(tabFromHash());
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  function selectTab(next: ProviderTab) {
    setTab(next);
    if (typeof window !== "undefined") {
      // replaceState keeps the back button from filling with tab flips.
      window.history.replaceState(null, "", `#${next}`);
    }
  }

  return { tab, selectTab };
}
