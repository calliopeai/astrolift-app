"use client";
import { useMemo } from "react";
import { useQuery } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import { useMe } from "@/graphql/user/user.hooks";
import { useLocalListState, type ListDefinition } from "@/components/list/use-list-state";
import { LIST_MODEL_CONNECTION_REQUESTS } from "@/graphql/models/model-connections.queries";
import type {
  ListModelConnectionRequestsQuery,
  ListModelConnectionRequestsQueryVariables,
} from "@/graphql/__generated__/operations";
import { useModelConnectionSupport } from "./use-model-connection-context";
import type { ModelConnectionRequestsProps } from "./ModelConnectionRequestsScreen";
export function useModelConnectionRequests(
  review: boolean,
  onReview: (review: boolean) => void
): ModelConnectionRequestsProps {
  const t = useTranslations("models.shared.connections"),
    { org, loading: orgLoading, error: orgError } = useActiveOrg(),
    { user, loading: userLoading, error: userError } = useMe(),
    support = useModelConnectionSupport();
  const admitted =
    !!org?.id && !!user?.id && !orgLoading && !userLoading && !orgError && !userError;
  const definition = useMemo(
    () =>
      ({
        id: review ? "models.connectionInbox" : "models.connectionRequests",
        fields: [],
        searchPlaceholder: t("title"),
        searchable: false,
        defaultSort: [],
        views: [{ key: "all", label: t(review ? "reviewInbox" : "myRequests"), filters: {} }],
        paging: "numbered",
        pageSizes: [10, 25, 50],
        defaultPageSize: 25,
      }) satisfies ListDefinition,
    [review, t]
  );
  const list = useLocalListState(definition);
  const query = useQuery<
    ListModelConnectionRequestsQuery,
    ListModelConnectionRequestsQueryVariables
  >(LIST_MODEL_CONNECTION_REQUESTS, {
    variables: {
      organizationId: org?.id ?? "",
      page: list.state.page,
      pageSize: list.state.pageSize,
      review,
    },
    skip: !admitted || support.supported !== true,
    fetchPolicy: "no-cache",
    context: { queryDeduplication: false },
  });
  const data = query.data ?? (query.error || query.loading ? query.previousData : undefined),
    page = admitted && support.supported === true ? (review ? data?.inbox : data?.own) : null;
  const invalid =
    !!page &&
    (page.page !== list.state.page ||
      page.pageSize !== list.state.pageSize ||
      page.items.some(
        (row) =>
          row.organizationId !== org?.id ||
          !row.id ||
          !Number.isSafeInteger(row.version) ||
          row.version < 1
      ));
  const error =
    orgError?.message ??
    userError?.message ??
    (invalid ? t("changed") : (query.error?.message ?? null));
  return {
    review,
    onReview,
    supported: support.supported,
    supportError: support.error,
    onRetrySupport: support.onRetry,
    page: {
      list,
      rows: invalid ? [] : (page?.items ?? []),
      loading: orgLoading || userLoading || (query.loading && !page),
      stale: !admitted || (!!page && (query.loading || !!error)),
      error: error ? { message: error } : null,
      totalCount: page?.totalCount ?? null,
      nextCursor: null,
      onRetry: () => {
        if (admitted && support.supported === true) void query.refetch().catch(() => {});
      },
    },
  };
}
