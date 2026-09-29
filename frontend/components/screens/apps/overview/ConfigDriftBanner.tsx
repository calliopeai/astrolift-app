"use client";

/**
 * ConfigDriftBannerView (#407 C): a notice in the overview's notices slot when
 * the latest applied deploy snapshot diverges from the live manifest,
 * or the source repo is ahead of the platform DB.
 *
 * Each entry in `configDrift.fields` is a stable field-path string
 * (`manifest_hash`, `image_tag`, `repo_unsynced`). The component
 * renders one bullet per entry with copy from i18n; unknown future
 * paths fall through to a generic "field changed" line so an FE
 * older than the backend never crashes.
 *
 * The CTA fires `RESYNC_MANIFEST_FROM_REPO` (#386) — the existing
 * resync mutation pulls the deploy-branch manifest and reconciles
 * workloads, env, managed services, and schedules. The mutation is
 * non-destructive on staged drafts; we don't gate it behind a
 * confirm dialog because it can't lose work.
 */

import { GitPullRequestArrowIcon, Loader2Icon } from "lucide-react";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import type { AstroliftAppConfigDrift } from "@/graphql/registry/registry.types";

import { Notice } from "./OverviewNotices";
import type { useConfigDriftResync } from "./use-config-drift-resync";

export type ConfigDriftBannerViewProps = ReturnType<typeof useConfigDriftResync> & {
  drift: AstroliftAppConfigDrift;
};

// Known drift field-paths. The FE renders explicit copy for these; any
// other path falls through to a generic line so a newer backend doesn't
// require a frontend deploy to render correctly.
const KNOWN_FIELDS = new Set<string>(["manifest_hash", "image_tag", "repo_unsynced"]);

export function ConfigDriftBannerView({ drift, resyncing, onResync }: ConfigDriftBannerViewProps) {
  const t = useTranslations("apps.detail.configDrift");

  if (!drift.hasDrift || drift.fields.length === 0) return null;

  return (
    <Notice
      tone="warning"
      icon={GitPullRequestArrowIcon}
      title={t("headline")}
      description={
        drift.environmentName
          ? t("description", { env: drift.environmentName })
          : t("descriptionNoEnv")
      }
      actions={
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={onResync}
          disabled={resyncing}
          className="gap-1"
        >
          {resyncing ? (
            <Loader2Icon className="size-4 animate-spin" />
          ) : (
            <GitPullRequestArrowIcon className="size-4" />
          )}
          {t("cta")}
        </Button>
      }
    >
      <ul className="space-y-0.5">
        {drift.fields.map((f) => (
          <li key={f} className="font-mono [overflow-wrap:anywhere]">
            {KNOWN_FIELDS.has(f) ? t(`field.${f}`) : t("field.unknown", { field: f })}
          </li>
        ))}
      </ul>
    </Notice>
  );
}
