"use client";

/**
 * ConfigDriftBanner (#407 C) — amber callout above the URL card when
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

import { useMutation } from "@apollo/client/react";
import { GitPullRequestArrowIcon, Loader2Icon } from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import type { MutationResult } from "@/graphql/identity/identity.types";
import { RESYNC_MANIFEST_FROM_REPO } from "@/graphql/registry/registry.mutations";
import { GET_APP } from "@/graphql/registry/registry.queries";
import type { AstroliftAppConfigDrift } from "@/graphql/registry/registry.types";

interface ResyncResp {
  resyncAstroliftManifestFromRepo: MutationResult<{
    syncState: string;
    summary: string;
    workloadsAdded: string[];
    workloadsRemoved: string[];
    workloadsChanged: string[];
    managedServicesAdded: string[];
    managedServicesRemoved: string[];
    envKeysChanged: number;
    schedulesChanged: number;
  }>;
}

interface Props {
  appSlug: string;
  drift: AstroliftAppConfigDrift;
}

// Known drift field-paths. The FE renders explicit copy for these; any
// other path falls through to a generic line so a newer backend doesn't
// require a frontend deploy to render correctly.
const KNOWN_FIELDS = new Set<string>(["manifest_hash", "image_tag", "repo_unsynced"]);

export function ConfigDriftBanner({ appSlug, drift }: Props) {
  const t = useTranslations("apps.detail.configDrift");
  const [resync, { loading }] = useMutation<ResyncResp>(RESYNC_MANIFEST_FROM_REPO, {
    refetchQueries: [{ query: GET_APP, variables: { slug: appSlug, includeDrift: true } }],
    awaitRefetchQueries: true,
  });

  if (!drift.hasDrift || drift.fields.length === 0) return null;

  async function handleResync() {
    try {
      const { data } = await resync({ variables: { input: { appSlug } } });
      const payload = data?.resyncAstroliftManifestFromRepo;
      if (!payload?.ok) {
        toast.error(payload?.errors?.[0]?.message ?? t("toast.failed"));
        return;
      }
      toast.success(payload.data?.summary ?? t("toast.synced"));
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t("toast.failed"));
    }
  }

  return (
    <section
      className="rounded-md border border-warning-border bg-warning/10 p-4"
      aria-live="polite"
    >
      <div className="flex items-start gap-3">
        <GitPullRequestArrowIcon
          aria-hidden
          className="size-5 shrink-0 text-warning-fg"
        />
        <div className="min-w-0 flex-1 space-y-2">
          <div>
            <p className="text-sm font-semibold text-warning-fg">
              {t("headline")}
            </p>
            <p className="text-xs leading-snug text-warning-fg">
              {drift.environmentName
                ? t("description", { env: drift.environmentName })
                : t("descriptionNoEnv")}
            </p>
          </div>
          <ul className="list-disc space-y-0.5 pl-5 text-xs text-warning-fg">
            {drift.fields.map((f) => (
              <li key={f} className="font-mono">
                {KNOWN_FIELDS.has(f) ? t(`field.${f}`) : t("field.unknown", { field: f })}
              </li>
            ))}
          </ul>
        </div>
        <Button
          type="button"
          size="sm"
          onClick={handleResync}
          disabled={loading}
          className="shrink-0 gap-1"
        >
          {loading ? (
            <Loader2Icon className="size-4 animate-spin" />
          ) : (
            <GitPullRequestArrowIcon className="size-4" />
          )}
          {t("cta")}
        </Button>
      </div>
    </section>
  );
}
