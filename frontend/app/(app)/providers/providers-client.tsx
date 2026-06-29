"use client";

import { CloudIcon, FingerprintIcon, GitBranchIcon } from "lucide-react";
import * as React from "react";

import { PageShell } from "@/components/PageShell";

import { IdentityProvidersPanel } from "../settings/identity-provider/identity-provider-client";
import { SourceProvidersPanel } from "../settings/source-providers/source-providers-client";
import { CloudProvidersPanel } from "./cloud-providers-panel";

type ProviderTab = "cloud" | "source" | "identity";

const TABS: { value: ProviderTab; label: string; icon: React.ReactNode }[] = [
  { value: "cloud", label: "Cloud", icon: <CloudIcon className="size-4" /> },
  { value: "source", label: "Source", icon: <GitBranchIcon className="size-4" /> },
  { value: "identity", label: "Identity", icon: <FingerprintIcon className="size-4" /> },
];

const VALID_TABS = new Set<ProviderTab>(["cloud", "source", "identity"]);

function tabFromHash(): ProviderTab {
  if (typeof window === "undefined") return "cloud";
  const h = window.location.hash.replace(/^#/, "") as ProviderTab;
  return VALID_TABS.has(h) ? h : "cloud";
}

/**
 * Unified Providers page (#887, #889, #890).
 *
 * One place for every external integration the org connects, split into
 * Cloud (driver bundles backing clusters), Source (SCM connections), and
 * Identity (IdP / SSO). Each tab reuses its existing per-domain panel and
 * queries — nothing is rewritten, just composed under shared chrome. The
 * active tab is mirrored to the URL hash so the folded
 * /settings/source-providers and /settings/identity-provider routes can
 * redirect straight to the right section.
 */
export function ProvidersClient() {
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

  return (
    <PageShell
      title="Providers"
      description="External integrations this organization connects: cloud driver bundles, source-code hosts, and identity providers. Each section shows what's wired plus how to connect more."
    >
      <div
        className="bg-muted/40 inline-flex rounded-md border p-1"
        role="tablist"
        aria-label="Provider categories"
      >
        {TABS.map((opt) => {
          const active = opt.value === tab;
          return (
            <button
              key={opt.value}
              type="button"
              role="tab"
              aria-selected={active}
              onClick={() => selectTab(opt.value)}
              className={
                "inline-flex items-center gap-1.5 rounded px-3 py-1 text-sm font-medium transition " +
                (active
                  ? "bg-background text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground")
              }
            >
              {opt.icon}
              {opt.label}
            </button>
          );
        })}
      </div>

      {tab === "cloud" && <CloudProvidersPanel />}
      {tab === "source" && <SourceProvidersPanel />}
      {tab === "identity" && <IdentityProvidersPanel />}
    </PageShell>
  );
}
