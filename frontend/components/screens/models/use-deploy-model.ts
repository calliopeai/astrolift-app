"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { toast } from "sonner";

import type {
  ListClusterGpusQuery,
  ListModelTargetsQuery,
} from "@/graphql/__generated__/operations";
import { LIST_CLUSTER_GPUS, LIST_MODEL_TARGETS } from "@/graphql/models/models.queries";
import { PROVISION_MANAGED_SERVICE } from "@/graphql/services/services.mutations";

import { serviceNameFor } from "./model-catalog";

// PROVISION_MANAGED_SERVICE interpolates its field list, so codegen leaves it
// untyped; these are the only fields this sheet reads.
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
 * the Deploy model sheet. Queries run only while the sheet is open. The data
 * half of DeployModelSheetView.
 */
export function useDeployModel(open: boolean) {
  const targets = useQuery<ListModelTargetsQuery>(LIST_MODEL_TARGETS, { skip: !open });
  const clusters = useQuery<ListClusterGpusQuery>(LIST_CLUSTER_GPUS, {
    skip: !open,
    errorPolicy: "all",
  });
  const [provision, { loading }] = useMutation<ProvisionResult>(PROVISION_MANAGED_SERVICE);

  /** Resolves true when the deploy was accepted (close the sheet). */
  async function deploy({ env, modelId, config }: DeployModelInput): Promise<boolean> {
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
      return true;
    }
    toast.error(result?.errors?.[0]?.message ?? "Deploy failed");
    return false;
  }

  return {
    envs: targets.data?.astroliftEnvironments ?? [],
    clusters: clusters.data?.astroliftClusters ?? [],
    loading,
    deploy,
  };
}
