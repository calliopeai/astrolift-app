"use client";
import { useEffect, useState } from "react";
import { useQuery } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import { useMe } from "@/graphql/user/user.hooks";
import type {
  ClusterModelFieldsFragment,
  GetModelSubscriptionMetricsQuery,
  GetModelSubscriptionMetricsQueryVariables,
} from "@/graphql/__generated__/operations";
import { GET_MODEL_SUBSCRIPTION_METRICS } from "./subscription-usage.queries";
import { modelObservationWindow } from "@/components/screens/models/use-model-observations";
import type { SubscriptionUsageData } from "@/components/screens/models/ModelSubscriptionUsagePanel";

const states = new Set([
  "AVAILABLE",
  "STALE",
  "NO_DATA",
  "UNCONFIGURED",
  "UNAVAILABLE",
  "UNSUPPORTED",
]);
const measures = {
  requests_per_second: "requests/s",
  error_requests_per_second: "requests/s",
  response_bytes_per_second: "bytes/s",
  latency_p95: "seconds",
};
// Receipts identify the exact authenticated subscription; aggregate model data is never admitted here.
export function subscriptionUsageReceipt(
  data: SubscriptionUsageData,
  model: ClusterModelFieldsFragment,
  subscriptionId: string
): boolean {
  return (
    data.serviceId === model.id &&
    data.clusterId === model.clusterId &&
    data.subscriptionId === subscriptionId &&
    data.scope === "authenticated_subscription" &&
    Number.isFinite(Date.parse(data.start)) &&
    Number.isFinite(Date.parse(data.end)) &&
    Date.parse(data.end) > Date.parse(data.start) &&
    Number.isFinite(Date.parse(data.retrievedAt)) &&
    Number.isFinite(data.stepSeconds) &&
    data.stepSeconds > 0 &&
    Array.isArray(data.metrics) &&
    Object.entries(measures).every(([key, unit]) =>
      data.metrics.some((metric) => metric.key === key && metric.unit === unit)
    ) &&
    new Set(data.metrics.map((metric) => metric.key)).size === data.metrics.length &&
    data.metrics.every(
      (metric) =>
        states.has(metric.state) &&
        metric.source === "authenticated_model_subscription" &&
        (metric.value === null ||
          (typeof metric.value === "number" && Number.isFinite(metric.value) && metric.value >= 0))
    )
  );
}
export const useModelSubscriptionUsage = (
  model: ClusterModelFieldsFragment,
  subscriptionId: string
) => {
  const t = useTranslations("models.shared.subscriptionUsage");
  const { org, loading: orgLoading, error: orgError } = useActiveOrg();
  const { user, loading: actorLoading, error: actorError } = useMe();
  const [window, setWindow] = useState<ReturnType<typeof modelObservationWindow> | null>(null);
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- Sample the external clock after hydration.
    setWindow(modelObservationWindow());
  }, []);
  const skipped =
    !window ||
    orgLoading ||
    actorLoading ||
    Boolean(orgError || actorError) ||
    !user?.id ||
    org?.id !== model.organizationId;
  const query = useQuery<
    GetModelSubscriptionMetricsQuery,
    GetModelSubscriptionMetricsQueryVariables
  >(GET_MODEL_SUBSCRIPTION_METRICS, {
    variables: {
      organizationId: model.organizationId,
      serviceId: model.id,
      subscriptionId,
      expectedClusterId: model.clusterId,
      expectedProviderId: model.providerId,
      start: window?.start ?? "",
      end: window?.end ?? "",
    },
    skip: skipped,
    fetchPolicy: "no-cache",
    context: { queryDeduplication: false },
  });
  const fresh = skipped ? null : query.data?.astroliftModelSubscriptionMetrics;
  const previous = skipped
    ? null
    : query.loading || query.error
      ? query.previousData?.astroliftModelSubscriptionMetrics
      : null;
  const receipt = fresh ?? previous;
  const wrong = Boolean(
    receipt &&
    (!subscriptionUsageReceipt(receipt, model, subscriptionId) ||
      (fresh &&
        (Date.parse(fresh.start) !== Date.parse(window!.start) ||
          Date.parse(fresh.end) !== Date.parse(window!.end))))
  );
  const scopeUnavailable =
    !orgLoading && !actorLoading && (!user?.id || org?.id !== model.organizationId);
  const missing = !skipped && !query.loading && !query.error && !fresh;
  return {
    read: {
      data: wrong ? null : (receipt ?? null),
      loading: orgLoading || actorLoading || !window || (!skipped && query.loading),
      stale: Boolean(receipt && !wrong && (query.loading || query.error)),
      error:
        orgError?.message ??
        actorError?.message ??
        (wrong || scopeUnavailable || missing ? t("unavailable") : (query.error?.message ?? null)),
    },
    onRefresh: () => {
      if (!skipped && !query.loading) setWindow(modelObservationWindow());
    },
  };
};
