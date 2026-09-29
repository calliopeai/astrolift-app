"use client";

import { useMutation } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import type { MutationResult } from "@/graphql/identity/identity.types";
import { RESYNC_MANIFEST_FROM_REPO } from "@/graphql/registry/registry.mutations";
import { GET_APP } from "@/graphql/registry/registry.queries";

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

/**
 * Fires RESYNC_MANIFEST_FROM_REPO (#386) and refetches the app. The data
 * half of ConfigDriftBannerView (#407 C).
 */
export function useConfigDriftResync(appSlug: string) {
  const t = useTranslations("apps.detail.configDrift");
  const [resync, { loading }] = useMutation<ResyncResp>(RESYNC_MANIFEST_FROM_REPO, {
    refetchQueries: [{ query: GET_APP, variables: { slug: appSlug, includeDrift: true } }],
    awaitRefetchQueries: true,
  });

  async function onResync() {
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

  return { resyncing: loading, onResync };
}
