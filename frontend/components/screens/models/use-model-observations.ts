"use client";
import { useEffect, useState } from "react";
import { useQuery } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import {
  GET_MODEL_DEPLOYMENT_METRICS,
  GET_CLUSTER_MODEL_DENSITY,
} from "@/graphql/models/observations.queries";
import type {
  ClusterModelFieldsFragment,
  GetModelDeploymentMetricsQuery,
  GetModelDeploymentMetricsQueryVariables,
  GetClusterModelDensityQuery,
  GetClusterModelDensityQueryVariables,
} from "@/graphql/__generated__/operations";
import type { ModelObservationsPanelProps } from "./ModelObservationsPanel";
export function modelObservationWindow(now = new Date()): { start: string; end: string } {
  return { start: new Date(now.getTime() - 15 * 60 * 1000).toISOString(), end: now.toISOString() };
}
export function useModelObservations(
  model: ClusterModelFieldsFragment
): ModelObservationsPanelProps {
  const t = useTranslations("models.shared.observations");
  const { org, loading: orgLoading, error: orgError } = useActiveOrg();
  const [window, setWindow] = useState<ReturnType<typeof modelObservationWindow> | null>(null);
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- Read the external clock after hydration so server and first client render agree.
    setWindow(modelObservationWindow());
  }, []);
  const skipped = !window || orgLoading || Boolean(orgError) || org?.id !== model.organizationId;
  // Both responses are implicitly tenant-scoped. Never reuse a cached or deduplicated response from another organization on a shared physical cluster.
  const queryOptions = {
    skip: skipped,
    fetchPolicy: "no-cache" as const,
    context: { queryDeduplication: false },
  };
  const metrics = useQuery<GetModelDeploymentMetricsQuery, GetModelDeploymentMetricsQueryVariables>(
    GET_MODEL_DEPLOYMENT_METRICS,
    {
      ...queryOptions,
      variables: {
        serviceId: model.id,
        expectedClusterId: model.clusterId,
        expectedProviderId: model.providerId,
        start: window?.start ?? "",
        end: window?.end ?? "",
      },
    }
  );
  const density = useQuery<GetClusterModelDensityQuery, GetClusterModelDensityQueryVariables>(
    GET_CLUSTER_MODEL_DENSITY,
    {
      ...queryOptions,
      variables: {
        clusterId: model.clusterId,
        expectedProviderId: model.providerId,
        start: window?.start ?? "",
        end: window?.end ?? "",
      },
    }
  );
  const metricResult = skipped
    ? null
    : (metrics.data ?? (metrics.loading || metrics.error ? metrics.previousData : undefined))
        ?.astroliftModelDeploymentMetrics;
  const densityResult = skipped
    ? null
    : (density.data ?? (density.loading || density.error ? density.previousData : undefined))
        ?.astroliftClusterModelDensity;
  const wrongMetric = Boolean(
    metricResult &&
    (metricResult.serviceId !== model.id ||
      metricResult.clusterId !== model.clusterId ||
      metricResult.scope !== "deployment_aggregate_not_app_attributed")
  );
  const wrongDensity = Boolean(
    densityResult &&
    (densityResult.clusterId !== model.clusterId ||
      densityResult.scope !== "organization_cluster_owned_models" ||
      densityResult.returnedCount !== densityResult.items.length ||
      densityResult.inventoryLimit !== 20 ||
      densityResult.returnedCount > densityResult.inventoryLimit ||
      densityResult.returnedCount < 0 ||
      new Set(densityResult.items.map((row) => row.serviceId)).size !==
        densityResult.items.length ||
      densityResult.modelCount < densityResult.returnedCount)
  );
  const scopeError =
    orgError?.message ??
    (!orgLoading && org?.id !== model.organizationId ? t("identityMismatch") : null);
  return {
    clusterId: model.clusterId,
    clusterName: model.clusterName,
    organizationId: model.organizationId,
    metrics: {
      data: wrongMetric ? null : (metricResult ?? null),
      loading: orgLoading || !window || (!skipped && metrics.loading),
      stale: Boolean(metricResult && (metrics.loading || metrics.error)),
      error:
        scopeError ??
        (wrongMetric ? t("identityMismatch") : skipped ? null : (metrics.error?.message ?? null)),
    },
    density: {
      data: wrongDensity ? null : (densityResult ?? null),
      loading: orgLoading || !window || (!skipped && density.loading),
      stale: Boolean(densityResult && (density.loading || density.error)),
      error:
        scopeError ??
        (wrongDensity ? t("identityMismatch") : skipped ? null : (density.error?.message ?? null)),
    },
    onRefresh: () => {
      if (!skipped) setWindow(modelObservationWindow());
    },
  };
}
