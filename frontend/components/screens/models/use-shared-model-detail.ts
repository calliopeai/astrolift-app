"use client";

import { useQuery } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import { GET_CLUSTER_MODEL_DEPLOYMENT } from "@/graphql/models/shared-models.queries";
import type {
  GetClusterModelDeploymentQuery,
  GetClusterModelDeploymentQueryVariables,
} from "@/graphql/__generated__/operations";
import type { SharedModelDetailScreenProps } from "./SharedModelDetailScreen";

export function useSharedModelDetail(id: string): SharedModelDetailScreenProps {
  const t = useTranslations("models.shared.detail");
  const { org, loading: orgLoading, error: orgError } = useActiveOrg();
  const organizationId = org?.id ?? "";
  const skipped = !organizationId || !id || orgLoading || Boolean(orgError);
  const query = useQuery<GetClusterModelDeploymentQuery, GetClusterModelDeploymentQueryVariables>(
    GET_CLUSTER_MODEL_DEPLOYMENT,
    { variables: { organizationId, id }, skip: skipped, fetchPolicy: "cache-and-network" }
  );
  const result = skipped ? null : query.data?.clusterModelDeployment;
  const invalidIdentity = Boolean(
    result && (result.id !== id || result.organizationId !== organizationId)
  );
  const model = invalidIdentity ? null : (result ?? null);
  return {
    model,
    loading: orgLoading || (!skipped && query.loading && !model && !query.error),
    stale: Boolean(model && (query.loading || query.error)),
    error:
      orgError?.message ??
      (invalidIdentity
        ? t("identityMismatch")
        : !skipped
          ? (query.error?.message ?? null)
          : !orgLoading
            ? t("missing")
            : null),
    onRetry: () => {
      if (!skipped) void query.refetch().catch(() => {});
    },
    subscriptions: null,
    observations: null,
    prompt: null,
  };
}
