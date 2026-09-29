"use client";

import { useMutation } from "@apollo/client/react";
import { toast } from "sonner";

import type { MutationResult } from "@/graphql/identity/identity.types";
import { SET_APP_SUBDOMAIN } from "@/graphql/registry/registry.mutations";
import { GET_APP } from "@/graphql/registry/registry.queries";
import type { ProvisioningStatus } from "@/graphql/registry/registry.types";

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
}

interface SetSubdomainResp {
  setAppSubdomain: MutationResult<{ id: string; slug: string; subdomain: string }>;
}

/**
 * Data half of UrlCardView (#408): the subdomain rename mutation, the
 * copy-to-clipboard action and the routability read of the app.
 */
export function useUrlCard({
  appId,
  appSlug,
  subdomain,
  managedHostname,
  primaryWorkloadSlug,
  provisioningStatus,
}: UseUrlCardArgs) {
  const [mutate, { loading: saving }] = useMutation<SetSubdomainResp>(SET_APP_SUBDOMAIN, {
    refetchQueries: [{ query: GET_APP, variables: { slug: appSlug } }],
    awaitRefetchQueries: true,
  });

  // Use the backend-computed managed hostname when available; fall back to the
  // short subdomain label for installs without a managed domain configured.
  const fullHost = managedHostname || subdomain;

  // Primary URL probe is gated on a deployed public workload — the
  // subdomain alone has no live origin to probe (DNS may resolve, but
  // nothing serves traffic). Showing a probe badge against a
  // non-deployed host would always read as "down" and panic operators.
  //
  // Routable === backend says provisioning is ready AND there's a
  // public workload to receive traffic. Anything else means the host
  // is either still being brought up or has nothing serving on it yet,
  // and the URL probe would always read as down. Surface a friendlier
  // "provisioning · ~2 min" badge instead. (#407 B)
  // isRoutable: full platform-managed deploy exists (workload live + cluster
  // reports ready). OR the managed domain is already resolving (app deployed
  // outside the platform pipeline) — in that case the URL IS live and we
  // should probe it rather than show the "not yet assigned" badge.
  const isRoutable = (provisioningStatus === "ready" && !!primaryWorkloadSlug) || !!managedHostname;
  const isProvisioning = provisioningStatus === "pending" || provisioningStatus === "provisioning";

  /** Resolves true when the rename landed, so the view can leave edit mode. */
  async function onSave(next: string): Promise<boolean> {
    const { data } = await mutate({
      variables: { input: { id: appId, subdomain: next } },
    });
    const env = data?.setAppSubdomain;
    if (!env) {
      toast.error("Subdomain change failed: no response from backend.");
      return false;
    }
    if (!env.ok) {
      toast.error(env.errors?.[0]?.message ?? "Subdomain change failed.");
      return false;
    }
    toast.success(`Subdomain changed to ${next}.`);
    return true;
  }

  async function onCopy() {
    try {
      await navigator.clipboard.writeText(`https://${fullHost}`);
      toast.success("URL copied to clipboard.");
    } catch {
      toast.error("Couldn't copy — clipboard unavailable.");
    }
  }

  return {
    subdomain,
    fullHost,
    isRoutable,
    isProvisioning,
    hasWorkload: !!primaryWorkloadSlug,
    saving,
    onSave,
    onCopy,
  };
}
