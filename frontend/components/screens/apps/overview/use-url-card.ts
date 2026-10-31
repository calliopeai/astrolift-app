"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import type { MutationResult } from "@/graphql/identity/identity.types";
import { REPROVISION_APP } from "@/graphql/lifecycle/lifecycle.mutations";
import { APP_REACHABILITY } from "@/graphql/lifecycle/lifecycle.queries";
import { SET_APP_SUBDOMAIN } from "@/graphql/registry/registry.mutations";
import { GET_APP } from "@/graphql/registry/registry.queries";
import type { ProvisioningStatus, ReachabilityStatus } from "@/graphql/registry/registry.types";

export interface UseUrlCardArgs {
  appId: string;
  appSlug: string;
  /** Short subdomain label (e.g. `my-app`). Used in the edit field. */
  subdomain: string;
  /**
   * Full platform-managed hostname (e.g. `my-app.astrolift.example.com`).
   * Computed by the backend from subdomain + ManagedDomain.zone. Empty when no
   * managed domain is configured — falls back to subdomain for display.
   */
  managedHostname: string;
  /** First public workload slug — used to determine routability. */
  primaryWorkloadSlug: string | null;
  /**
   * Provisioning status of the parent app. Used to render the pending
   * URL placeholder (#407 B) when the host isn't routable yet.
   */
  provisioningStatus: ProvisioningStatus;
  /** Whether the app's public hosts answer (#2573); the card's state follows it. */
  reachabilityStatus: ReachabilityStatus;
}

interface SetSubdomainResp {
  setAppSubdomain: MutationResult<{ id: string; slug: string; subdomain: string }>;
}

interface ReprovisionResp {
  reprovisionApp: MutationResult<{
    appSlug: string;
    environments: {
      environmentName: string;
      deploymentId: string | null;
      reachabilityStatus: ReachabilityStatus;
      objectsCreated: string[];
      objectsUpdated: string[];
      unchanged: boolean;
    }[];
  }>;
}

interface ReachabilityResp {
  astroliftEnvironments: {
    id: string;
    name: string;
    kind: string;
    reachabilityStatus: ReachabilityStatus;
    reachabilityDetail: string;
  }[];
}

/**
 * Why an app that is not reachable is not (#2573): the detail of the
 * environment that set its status. That is a non-preview one, as the app's
 * status is theirs, and previews sort before production by name.
 */
export function unreachableDetail(
  environments: ReachabilityResp["astroliftEnvironments"],
  status: ReachabilityStatus
): string | null {
  if (status === "REACHABLE") return null;
  const candidates = environments.filter((env) => env.kind !== "preview" && env.reachabilityDetail);
  const env =
    candidates.find((e) => e.reachabilityStatus === status) ??
    candidates.find((e) => e.reachabilityStatus !== "REACHABLE");
  return env ? `${env.name}: ${env.reachabilityDetail}` : null;
}

/**
 * Data half of UrlCardView (#408): the subdomain rename mutation, the
 * copy-to-clipboard action, the routability read of the app, and (#2573) the
 * reprovision action with what each environment is missing.
 */
export function useUrlCard({
  appId,
  appSlug,
  subdomain,
  managedHostname,
  primaryWorkloadSlug,
  provisioningStatus,
  reachabilityStatus,
}: UseUrlCardArgs) {
  const t = useTranslations("appOverviewCards.url");
  const [mutate, { loading: saving }] = useMutation<SetSubdomainResp>(SET_APP_SUBDOMAIN, {
    refetchQueries: [{ query: GET_APP, variables: { slug: appSlug } }],
    awaitRefetchQueries: true,
  });
  const reachable = reachabilityStatus === "REACHABLE";
  // Only an app that is not reachable needs to say why.
  const detailQ = useQuery<ReachabilityResp>(APP_REACHABILITY, {
    variables: { appSlug },
    skip: reachable,
    fetchPolicy: "cache-and-network",
  });
  const [reprovision, { loading: reprovisioning }] = useMutation<ReprovisionResp>(REPROVISION_APP, {
    refetchQueries: [
      { query: GET_APP, variables: { slug: appSlug } },
      { query: APP_REACHABILITY, variables: { appSlug } },
    ],
    awaitRefetchQueries: true,
  });

  // Use the backend-computed managed hostname when available; fall back to the
  // short subdomain label for installs without a managed domain configured.
  const fullHost = managedHostname || subdomain;

  // The live probe only means something once the host answers (#2573): before
  // that the Ingress, certificate or claim behind it may not exist, and a
  // probe badge would read as an outage.
  const isRoutable = reachable && !!primaryWorkloadSlug;
  const isProvisioning =
    provisioningStatus === "pending" ||
    provisioningStatus === "provisioning" ||
    reachabilityStatus === "PROVISIONING";
  const reachabilityDetail = unreachableDetail(
    detailQ.data?.astroliftEnvironments ?? [],
    reachabilityStatus
  );

  /** Resolves true when the rename landed, so the view can leave edit mode. */
  async function onSave(next: string): Promise<boolean> {
    const { data } = await mutate({
      variables: { input: { id: appId, subdomain: next } },
    });
    const env = data?.setAppSubdomain;
    if (!env) {
      toast.error(t("toast.noResponse"));
      return false;
    }
    if (!env.ok) {
      toast.error(env.errors?.[0]?.message ?? t("toast.failed"));
      return false;
    }
    toast.success(t("toast.changed", { subdomain: next }));
    return true;
  }

  async function onCopy() {
    try {
      await navigator.clipboard.writeText(`https://${fullHost}`);
      toast.success(t("toast.copied"));
    } catch {
      toast.error(t("toast.copyFailed"));
    }
  }

  async function onReprovision() {
    let data;
    try {
      ({ data } = await reprovision({ variables: { input: { appSlug } } }));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("toast.reprovisionFailed"));
      return;
    }
    const result = data?.reprovisionApp;
    if (!result) {
      toast.error(t("toast.reprovisionNoResponse"));
      return;
    }
    if (!result.ok) {
      toast.error(result.errors?.[0]?.message ?? t("toast.reprovisionFailed"));
      return;
    }
    const environments = result.data?.environments ?? [];
    if (environments.some((env) => env.reachabilityStatus === "PROVISIONING" && !env.unchanged)) {
      toast.success(t("toast.reprovisionRunning"));
    } else if (environments.every((env) => env.unchanged)) {
      toast.success(t("toast.reprovisionUnchanged"));
    } else {
      toast.success(
        t("toast.reprovisionDone", {
          created: environments.reduce((n, env) => n + env.objectsCreated.length, 0),
          updated: environments.reduce((n, env) => n + env.objectsUpdated.length, 0),
        })
      );
    }
  }

  return {
    subdomain,
    fullHost,
    isRoutable,
    isProvisioning,
    hasWorkload: !!primaryWorkloadSlug,
    reachabilityStatus,
    reachabilityDetail,
    saving,
    reprovisioning,
    onSave,
    onCopy,
    onReprovision,
  };
}
