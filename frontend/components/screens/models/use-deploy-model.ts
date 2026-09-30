"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";

import type {
  ListClusterGpusQuery,
  ListModelTargetsQuery,
} from "@/graphql/__generated__/operations";
import { LIST_CLUSTER_GPUS, LIST_MODEL_TARGETS } from "@/graphql/models/models.queries";
import { PROVISION_MANAGED_SERVICE } from "@/graphql/services/services.mutations";

import { serviceNameFor } from "./model-catalog";

// PROVISION_MANAGED_SERVICE interpolates its field list, so codegen leaves it
// untyped; these are the only fields this page reads.
interface ProvisionResult {
  provisionManagedService: { ok: boolean; errors?: { message: string }[] | null };
}

export type ModelTarget = ListModelTargetsQuery["astroliftEnvironments"][number];
export type GpuCluster = ListClusterGpusQuery["astroliftClusters"][number];

export interface DeployModelInput {
  env: ModelTarget;
  modelId: string;
  config: Record<string, unknown>;
}

/**
 * Deploy targets, cluster GPU capabilities and the provision mutation behind
 * the Deploy model page. A refused deploy comes back as the reason for the
 * review step to show; an accepted one toasts and returns to Models. The
 * data half of DeployModelScreen.
 */
export function useDeployModel() {
  const router = useRouter();
  const targets = useQuery<ListModelTargetsQuery>(LIST_MODEL_TARGETS, {
    fetchPolicy: "cache-and-network",
  });
  const clusters = useQuery<ListClusterGpusQuery>(LIST_CLUSTER_GPUS, {
    fetchPolicy: "cache-and-network",
  });
  const [provision, { loading }] = useMutation<ProvisionResult>(PROVISION_MANAGED_SERVICE);

  /** Resolves null when the deploy was accepted, or the reason it was refused. */
  async function deploy({ env, modelId, config }: DeployModelInput): Promise<string | null> {
    try {
      const { data } = await provision({
        variables: {
          input: {
            appSlug: env.registeredAppSlug,
            environmentName: env.name,
            kind: "model_endpoint",
            variant: "vllm",
            name: serviceNameFor(modelId),
            config,
          },
        },
      });
      const result = data?.provisionManagedService;
      if (result?.ok) {
        toast.success(`Deploying ${modelId} to ${env.registeredAppSlug} · ${env.name}`);
        router.push("/models");
        return null;
      }
      return result?.errors?.[0]?.message ?? "Deploy failed";
    } catch (err) {
      return err instanceof Error ? err.message : String(err);
    }
  }

  return {
    envs: targets.data?.astroliftEnvironments ?? [],
    envsLoading: targets.loading && !targets.data,
    envsError: targets.data ? null : (targets.error?.message ?? null),
    onRetryTargets: () => {
      void targets.refetch().catch(() => {});
    },
    clustersLoading: clusters.loading && !clusters.data,
    clustersError: clusters.data ? null : (clusters.error?.message ?? null),
    onRetryClusters: () => {
      void clusters.refetch().catch(() => {});
    },
    clusters: clusters.data?.astroliftClusters ?? [],
    loading,
    deploy,
  };
}

export type DeployModelState = ReturnType<typeof useDeployModel>;
