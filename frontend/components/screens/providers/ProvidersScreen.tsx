"use client";

import { CloudIcon, FingerprintIcon, GitBranchIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";

import { PageShell } from "@/components/PageShell";

import type { ProviderTab, useProvidersTab } from "./use-providers-tab";

const TABS: { value: ProviderTab; icon: React.ReactNode }[] = [
  { value: "cloud", icon: <CloudIcon className="size-4" /> },
  { value: "source", icon: <GitBranchIcon className="size-4" /> },
  { value: "identity", icon: <FingerprintIcon className="size-4" /> },
];

export type ProvidersScreenProps = ReturnType<typeof useProvidersTab> & {
  /** One panel per tab; only the active one is mounted, so only its hooks run. */
  panels: Record<ProviderTab, React.ReactNode>;
};

/**
 * Unified Providers page (#887, #889, #890).
 *
 * One place for every external integration the org connects, split into
 * Cloud (driver bundles backing clusters), Source (SCM connections), and
 * Identity (IdP / SSO). Each tab reuses its existing per-domain panel and
 * queries — nothing is rewritten, just composed under shared chrome.
 */
export function ProvidersScreen({ tab, selectTab, panels }: ProvidersScreenProps) {
  const t = useTranslations("providersScreen");
  return (
    <PageShell title={t("title")} description={t("description")}>
      <div
        className="bg-muted/40 inline-flex rounded-md border p-1"
        role="tablist"
        aria-label={t("categories")}
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
              {t(`tabs.${opt.value}`)}
            </button>
          );
        })}
      </div>

      {panels[tab]}
    </PageShell>
  );
}
