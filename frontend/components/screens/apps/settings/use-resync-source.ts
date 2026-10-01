"use client";

import { useMutation } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";

import type { MutationResult } from "@/graphql/identity/identity.types";
import { RESYNC_MANIFEST_FROM_REPO } from "@/graphql/registry/registry.mutations";
import { GET_APP } from "@/graphql/registry/registry.queries";
import type { AstroliftRegisteredApp } from "@/graphql/registry/registry.types";
import { refetchAfterMutation } from "@/lib/apollo/mutation-feedback";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

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

export type ResyncSource = Pick<
  AstroliftRegisteredApp,
  | "id"
  | "slug"
  | "version"
  | "organizationSlug"
  | "projectId"
  | "sourceKind"
  | "sourceRepo"
  | "sourceUrl"
  | "manifestPath"
  | "deployBranch"
  | "defaultBranch"
  | "manifestHash"
>;

export function useResyncSource(appSlug: string, source?: ResyncSource | null, agentMode = false) {
  const t = useTranslations("apps.settings.agentResyncFlow");
  const perms = useMyPermissions();
  const allowed = (perms.loading && perms.granted.size === 0) || perms.can("app.update");
  const observed = source === undefined || (!!source?.id && source.slug === appSlug);
  const fingerprint = JSON.stringify([
    appSlug,
    source?.id,
    source?.version,
    source?.organizationSlug,
    source?.projectId,
    source?.sourceKind,
    source?.sourceRepo,
    source?.sourceUrl,
    source?.manifestPath,
    source?.deployBranch,
    source?.defaultBranch,
    source?.manifestHash,
    agentMode,
    observed,
    allowed,
  ]);
  const context = React.useMemo(() => ({ fingerprint }), [fingerprint]);
  const current = React.useRef<object | null>(context);
  React.useLayoutEffect(() => {
    current.current = context;
    return () => {
      current.current = null;
    };
  }, [context]);
  const [pending, setPending] = React.useState<{ context: object; operation: object } | null>(null);
  const running = React.useRef<{ context: object; operation: object } | null>(null);
  const [resync] = useMutation<ResyncResp>(RESYNC_MANIFEST_FROM_REPO, { fetchPolicy: "no-cache" });

  async function onResync() {
    if (current.current !== context || !observed || !allowed) {
      toast.error(t("sourceChanged"));
      return;
    }
    if (running.current?.context === context) return;
    const operation = {};
    const active = { context, operation };
    running.current = active;
    setPending(active);
    try {
      const { data } = await resync({
        variables: { input: { appSlug } },
        refetchQueries: (reply) =>
          reply.data?.resyncAstroliftManifestFromRepo?.ok && current.current === context
            ? [{ query: GET_APP, variables: { slug: appSlug } }]
            : [],
        onQueryUpdated: (query) =>
          refetchAfterMutation(query, t("refreshWarning", { slug: appSlug })),
        awaitRefetchQueries: true,
      });
      const envelope = data?.resyncAstroliftManifestFromRepo;
      if (!envelope) {
        toast.error(t("noResponse"));
        return;
      }
      if (!envelope.ok) {
        toast.error(envelope.errors?.[0]?.message || t("failed"));
        return;
      }
      const payload = envelope.data;
      if (!payload) {
        toast.warning(t("acceptedWithoutDetails", { slug: appSlug }));
        return;
      }
      const description = payload.summary || undefined;
      if (payload.syncState === "in_sync") {
        toast.success(agentMode ? t("agentInSync", { slug: appSlug }) : t("inSync"), {
          description: agentMode ? description : undefined,
        });
      } else if (agentMode && payload.syncState === "applied") {
        toast.success(t("agentApplied", { slug: appSlug }), { description });
      } else if (payload.syncState === "applied") {
        toast.success(payload.summary || t("accepted", { slug: appSlug }));
      } else {
        toast.success(t("reportedState", { slug: appSlug, state: payload.syncState }), {
          description,
        });
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("failed"));
    } finally {
      if (running.current === active) running.current = null;
      setPending((value) => (value === active ? null : value));
    }
  }

  return { loading: pending?.context === context, onResync, unavailable: !observed };
}
