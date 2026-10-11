"use client";

import { useMemo } from "react";
import { useQuery } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import { useListState } from "@/components/list/use-list-state";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import { LIST_CLUSTER_MODELS_PAGE } from "@/graphql/models/shared-models.queries";
import type {
  ListClusterModelsPageQuery,
  ListClusterModelsPageQueryVariables,
} from "@/graphql/__generated__/operations";
import type { SharedModelsScreenProps } from "./SharedModelsScreen";
import { sharedModelsList, sharedModelsVariables } from "./shared-models-list";

export function useSharedModels(): SharedModelsScreenProps {
  const t = useTranslations("models.shared.deployments");
  const { org, loading: orgLoading, error: orgError } = useActiveOrg();
  const organizationId = org?.id ?? "";
  const definition = useMemo(
    () =>
      sharedModelsList({
        search: t("search"),
        all: t("all"),
        mine: t("mine"),
        mineNote: t("mineNote"),
        clusterId: t("clusterId"),
        compute: t("compute"),
        cpu: t("cpu"),
        gpu: t("gpu"),
        status: t("status"),
        readiness: t("readiness"),
        confirmed: t("confirmed"),
        unconfirmed: t("unconfirmed"),
        subscriptions: t("subscriptions"),
        enabled: t("enabled"),
        disabled: t("disabled"),
        statuses: {
          pending: t("statuses.pending"),
          provisioning: t("statuses.provisioning"),
          active: t("statuses.active"),
          updating: t("statuses.updating"),
          deprovisioning: t("statuses.deprovisioning"),
          failed: t("statuses.failed"),
        },
      }),
    [t]
  );
  const list = useListState(definition);
  const variables = sharedModelsVariables(
    organizationId,
    list.state.q,
    list.filters,
    list.state.page,
    list.state.pageSize
  );
  const skipped = !organizationId || orgLoading || Boolean(orgError) || variables === null;
  const query = useQuery<ListClusterModelsPageQuery, ListClusterModelsPageQueryVariables>(
    LIST_CLUSTER_MODELS_PAGE,
    {
      variables: variables ?? { organizationId, page: 1, pageSize: 25 },
      skip: skipped,
      fetchPolicy: "no-cache",
      context: { queryDeduplication: false },
    }
  );
  const page = skipped ? undefined : query.data?.clusterModelDeploymentsPage;
  const invalidIdentity = Boolean(page?.items.some((row) => row.organizationId !== organizationId));
  return {
    page: {
      list,
      rows: invalidIdentity
        ? []
        : (page?.items ?? []).map((row) => ({
            ...row,
            desiredResources: {
              cpuRequest: row.desiredResources.cpuRequest ?? null,
              memoryRequest: row.desiredResources.memoryRequest ?? null,
              gpuCount: row.desiredResources.gpuCount ?? null,
            },
            revisionSha: row.revisionSha ?? null,
            computeMode: row.computeMode ?? null,
            reason: row.reason ?? null,
            ready: row.ready ?? null,
            readinessObservedAt: row.readinessObservedAt ?? null,
          })),
      totalCount: invalidIdentity ? null : (page?.totalCount ?? null),
      nextCursor: null,
      loading: orgLoading || (!skipped && query.loading && !page && !query.error),
      stale: !skipped && query.loading && Boolean(page),
      error: orgError
        ? { message: orgError.message }
        : variables === null
          ? { message: t("invalidFilter") }
          : invalidIdentity
            ? { message: t("unavailable") }
            : query.error && !skipped
              ? { message: query.error.message }
              : !organizationId && !orgLoading
                ? { message: t("unavailable") }
                : null,
      onRetry: () => {
        if (!skipped) void query.refetch().catch(() => {});
      },
    },
  };
}
