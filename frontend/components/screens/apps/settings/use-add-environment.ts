"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { LIST_CLUSTERS } from "@/graphql/clusters/clusters.queries";
import type { MutationResult } from "@/graphql/identity/identity.types";
import { CREATE_APP_ENVIRONMENT } from "@/graphql/lifecycle/lifecycle.mutations";
import type { AstroliftAppEnvironment } from "@/graphql/lifecycle/lifecycle.types";

/** One DNS label, as the server requires: it lands in a namespace and a hostname. */
export const ENVIRONMENT_NAME = /^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$/;

export interface AddEnvironmentCluster {
  slug: string;
  name: string;
}

interface ClustersResp {
  astroliftClusters: (AddEnvironmentCluster & { lifecycle: string; isActive: boolean })[];
}

interface CreateResp {
  createAppEnvironment: MutationResult<AstroliftAppEnvironment>;
}

/**
 * Add an environment to an app (#2398). Offers the active managed clusters
 * the viewer can see; none (or no cluster read) leaves the app's default.
 */
export function useAddEnvironment(appSlug: string) {
  const t = useTranslations("apps.settings.addEnvironment");
  const clustersQuery = useQuery<ClustersResp>(LIST_CLUSTERS, { fetchPolicy: "cache-first" });
  const clusters: AddEnvironmentCluster[] = (clustersQuery.data?.astroliftClusters ?? [])
    .filter((c) => c.isActive && c.lifecycle === "managed")
    .map(({ slug, name }) => ({ slug, name }));
  const [create, { loading }] = useMutation<CreateResp>(CREATE_APP_ENVIRONMENT, {
    refetchQueries: (reply) =>
      reply.data?.createAppEnvironment?.ok ? ["ListEnvironmentsPage"] : [],
  });

  /** Resolves true when the environment was created (clear the form). */
  async function onCreate(name: string, clusterSlug: string | null): Promise<boolean> {
    try {
      const { data } = await create({
        variables: { input: { appSlug, name, clusterSlug } },
      });
      const result = data?.createAppEnvironment;
      if (!result?.ok) {
        toast.error(result?.errors?.[0]?.message ?? t("failed"));
        return false;
      }
      toast.success(t("created", { name }));
      return true;
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("failed"));
      return false;
    }
  }

  return { clusters, creating: loading, onCreate };
}
