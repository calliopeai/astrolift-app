"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { useApolloClient } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import { useScopedCursorTable } from "@/components/data-table/use-scoped-cursor-table";
import type { NvkeOperatorInstallReviewPanelProps } from "@/components/screens/nvke/NvkeOperatorInstallReviewPanel";
import type { NvkeOperatorJobPanelProps } from "@/components/screens/nvke/NvkeOperatorJobPanel";
import type {
  GetNvkeOperatorInstallWorkloadPageQuery,
  ReviewNvkeOperatorInstallMutation,
  ReviewNvkeOperatorInstallMutationVariables,
} from "@/graphql/__generated__/operations";
import {
  installReviewSchema,
  installWorkloadPageSchema,
  type NvkeInstallReview,
  type NvkeInstallWorkload,
} from "@/lib/nvke/operator-install-review";
import { useNvkeEpoch, type useNvkeContext } from "./nvke.hooks";
import { NVKE_REQUEST_CONTEXT } from "./use-scoped-read";
import { REVIEW_NVKE_OPERATOR_INSTALL } from "./nvke-operator.mutations";
import { GET_NVKE_OPERATOR_INSTALL_WORKLOAD_PAGE } from "./nvke-operator.queries";

export const useNvkeOperatorInstallReview = (args: {
  context: ReturnType<typeof useNvkeContext>;
  source: NvkeOperatorJobPanelProps | null;
  pending: boolean;
  onRefresh: () => void;
}): NvkeOperatorInstallReviewPanelProps | null => {
  const client = useApolloClient(),
    t = useTranslations("nvke");
  const { context, source } = args;
  const eligible =
    source?.operation.operationKey === "tooling.management.install" &&
    source.operation.status === "UNCERTAIN";
  const ready =
    !!eligible &&
    source.canManage &&
    !!source.authority &&
    !!context.scope &&
    context.access.admin &&
    context.access.read &&
    !context.access.loading &&
    !context.loading &&
    !context.failed;
  const [refreshEpoch, setRefreshEpoch] = useState(0);
  const { key, isCurrent: current } = useNvkeEpoch(
    JSON.stringify([
      ready,
      context.scopeKey,
      context.scope,
      source?.scopeKey,
      source?.site,
      source?.operation.id,
      source?.operation.version,
      source?.operation.status,
      source?.authority,
      refreshEpoch,
    ])
  );
  const [state, setState] = useState<{
    key: string;
    pending: boolean;
    review: NvkeInstallReview | null;
    error: string | null;
  } | null>(null);
  const busy = useRef(false);
  const visible = state?.key === key ? state : null;
  const review = ready && visible?.review ? visible.review : null;
  useEffect(() => {
    if (!review) return;
    const timer = setTimeout(
      () => {
        setState((value) =>
          value?.key === key
            ? { ...value, review: null, error: t("installReview.unavailable") }
            : value
        );
      },
      Math.min(2147483647, Math.max(0, Date.parse(review.snapshotExpiresAt) - Date.now()))
    );
    return () => clearTimeout(timer);
  }, [key, review, t]);
  const input = {
    operationId: source?.operation.id,
    expectedActorId: context.scope?.actorId,
    expectedSiteVersion: source?.site.version,
    expectedAuthorityVersion: source?.authority?.version,
    expectedOperationVersion: source?.operation.version,
  };
  const requestContext = {
    ...NVKE_REQUEST_CONTEXT,
    expectedNvkeOrganization: context.scope?.organizationId,
  };
  const validatePage = useCallback(
    (data: unknown, variables: Record<string, unknown>) => {
      const envelope = (data as GetNvkeOperatorInstallWorkloadPageQuery | undefined)
        ?.astroliftNvkeOperatorInstallWorkloadPage;
      const parsed = installWorkloadPageSchema.safeParse(envelope?.data);
      if (
        !envelope?.ok ||
        !Array.isArray(envelope.errors) ||
        envelope.errors.length ||
        !parsed.success ||
        !review
      )
        return false;
      const page = parsed.data;
      return (
        Date.parse(review.snapshotExpiresAt) > Date.now() &&
        page.snapshotId === review.snapshotId &&
        page.operationId === review.operationId &&
        page.operationVersion === review.operationVersion &&
        page.reviewDigest === review.reviewDigest &&
        page.totalCount <= review.workloadCount &&
        (variables.search ? true : page.totalCount === review.workloadCount) &&
        typeof variables.limit === "number" &&
        page.items.length <= variables.limit
      );
    },
    [review]
  );
  const table = useScopedCursorTable<NvkeInstallWorkload>(
    {
      query: GET_NVKE_OPERATOR_INSTALL_WORKLOAD_PAGE,
      variables: { input, snapshotId: review?.snapshotId },
      skip: !review,
      searchVariable: "search",
      emptySearchValue: "",
      extract: (data) => {
        const parsed = installWorkloadPageSchema.safeParse(
          (data as GetNvkeOperatorInstallWorkloadPageQuery | undefined)
            ?.astroliftNvkeOperatorInstallWorkloadPage.data
        );
        return parsed.success ? parsed.data : undefined;
      },
    },
    review ? JSON.stringify([key, review.snapshotId, review.reviewDigest]) : "",
    requestContext,
    validatePage
  );
  const onRefresh = () => {
    if (!current() || busy.current || args.pending) return;
    setState(null);
    setRefreshEpoch((value) => value + 1);
    args.onRefresh();
  };
  const onReview = async () => {
    if (
      !ready ||
      !source?.authority ||
      !context.scope ||
      !current() ||
      busy.current ||
      args.pending
    )
      return false;
    busy.current = true;
    setState({ key, pending: true, review: null, error: null });
    try {
      const response = await client.mutate<
        ReviewNvkeOperatorInstallMutation,
        ReviewNvkeOperatorInstallMutationVariables
      >({
        mutation: REVIEW_NVKE_OPERATOR_INSTALL,
        variables: {
          input: {
            operationId: source.operation.id,
            expectedActorId: context.scope.actorId,
            expectedSiteVersion: source.site.version,
            expectedAuthorityVersion: source.authority.version,
            expectedOperationVersion: source.operation.version,
          },
        },
        fetchPolicy: "no-cache",
        context: requestContext,
      });
      if (!current()) return false;
      const envelope = response.data?.reviewNvkeOperatorInstall;
      const parsed = installReviewSchema.safeParse(envelope?.data);
      if (
        !envelope?.ok ||
        !Array.isArray(envelope.errors) ||
        envelope.errors.length ||
        !parsed.success
      )
        throw new Error("INVALID_REVIEW");
      const value = parsed.data;
      if (
        value.siteId !== source.site.id ||
        value.siteVersion !== source.site.version ||
        value.operationId !== source.operation.id ||
        value.operationVersion !== source.operation.version ||
        value.authorityId !== source.authority.id ||
        value.authorityVersion !== source.authority.version ||
        value.instanceId !== source.authority.instanceId ||
        value.bundleManifestSha256 !== source.authority.bundleManifestSha256 ||
        value.nativeRevision !== source.site.upstreamRevision ||
        Date.parse(value.snapshotExpiresAt) <= Date.now()
      )
        throw new Error("SOURCE_CHANGED");
      setState({ key, pending: false, review: value, error: null });
      return true;
    } catch {
      if (current()) setState({ key, pending: false, review: null, error: t("requestFailed") });
      return false;
    } finally {
      busy.current = false;
    }
  };
  if (!eligible || !source) return null;
  return {
    scopeKey: key,
    site: source.site,
    operation: source.operation,
    authority: ready ? source.authority : null,
    review,
    table,
    loading: visible?.pending === true,
    pending: args.pending || visible?.pending === true,
    canReview: ready,
    error: visible?.error ?? null,
    onReview,
    onRefresh,
  };
};
