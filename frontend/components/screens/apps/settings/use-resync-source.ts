"use client";

import { useMutation } from "@apollo/client/react";
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
 * "Resync from source" (#386): re-fetches `astrolift.toml` from the deploy
 * branch and reconciles workloads / env / managed services / schedules. The
 * mutation is non-destructive on staged drafts; a CONFLICT from the backend
 * surfaces as an error toast so the draft survives. Refetches GET_APP so the
 * "Last resynced" timestamp re-renders.
 */
export function useResyncSource(appSlug: string) {
  const [resync, { loading }] = useMutation<ResyncResp>(RESYNC_MANIFEST_FROM_REPO, {
    refetchQueries: [{ query: GET_APP, variables: { slug: appSlug } }],
    awaitRefetchQueries: true,
  });

  async function onResync() {
    try {
      const { data } = await resync({ variables: { input: { appSlug } } });
      const env = data?.resyncAstroliftManifestFromRepo;
      if (!env) {
        toast.error("Resync failed: no response from backend.");
        return;
      }
      if (!env.ok) {
        toast.error(env.errors?.[0]?.message ?? "Resync failed.");
        return;
      }
      const payload = env.data;
      if (!payload) {
        toast.error("Resync returned no payload.");
        return;
      }
      if (payload.syncState === "in_sync") {
        toast.success("Already in sync.");
      } else {
        toast.success(payload.summary);
      }
    } catch (err) {
      // Apollo network error / unexpected throw — surface verbatim so
      // the operator can copy/paste into a ticket.
      toast.error(err instanceof Error ? err.message : "Resync failed.");
    }
  }

  return { loading, onResync };
}
