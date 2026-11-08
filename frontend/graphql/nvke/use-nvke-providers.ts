"use client";

import { useMemo, useState } from "react";
import { useTranslations } from "next-intl";
import { useScopedCursorTable } from "@/components/data-table/use-scoped-cursor-table";
import type {
  NvkeProviderIdentity,
  NvkeProviderId,
  NvkeProvidersPanelProps,
} from "@/components/screens/nvke/NvkeProvidersPanel";
import type { NvkeOperationView } from "@/components/screens/nvke/types";
import type {
  GetNvkeProviderObservationQuery,
  GetNvkeProviderIdentitiesQuery,
} from "@/graphql/__generated__/operations";
import {
  nvkeProviderObservationSchema,
  nvkeProviderIdentityPageSchema,
  nvkeProviderSourceSchema,
} from "@/lib/nvke/providers";
import type { useNvkeContext } from "./nvke.hooks";
import { NVKE_REQUEST_CONTEXT, useNvkeRead } from "./use-scoped-read";
import {
  GET_NVKE_PROVIDER_OBSERVATION,
  GET_NVKE_PROVIDER_IDENTITIES,
} from "./nvke-providers.queries";

export const useNvkeProviders = (args: {
  context: ReturnType<typeof useNvkeContext>;
  scopeKey: string;
  siteId: string;
  operation: NvkeOperationView | null;
  enabled: boolean;
}): NvkeProvidersPanelProps | null => {
  const { context, operation } = args,
    t = useTranslations("nvke");
  const source = nvkeProviderSourceSchema.safeParse(operation?.providerSource);
  const eligible =
    args.enabled &&
    !!context.scope &&
    !!args.scopeKey &&
    !context.failed &&
    !context.loading &&
    !context.access.loading &&
    context.access.read &&
    context.access.admin &&
    operation?.siteId === args.siteId &&
    operation.operationKey === "infrastructure.providers" &&
    operation.status === "SUCCEEDED" &&
    !operation.isInventory;
  const key = eligible
    ? JSON.stringify([
        args.scopeKey,
        args.siteId,
        operation.id,
        operation.version,
        operation.providerSource,
        context.access.read,
        context.access.admin,
      ])
    : "";
  const [selection, setSelection] = useState<{ key: string; provider: NvkeProviderId } | null>(
    null
  );
  const selected = selection?.key === key ? selection.provider : "nico";
  const variables = {
    siteId: args.siteId,
    operationId: operation?.id,
    expectedActorId: context.scope?.actorId,
    expectedVersion: operation?.version,
  };
  const read = useNvkeRead<GetNvkeProviderObservationQuery>(
    GET_NVKE_PROVIDER_OBSERVATION,
    variables,
    key && source.success ? key : "",
    0,
    context.scope?.organizationId
  );
  const envelope = read.data?.astroliftNvkeProviderObservation;
  const parsed = nvkeProviderObservationSchema.safeParse(envelope?.data);
  const data = parsed.success ? parsed.data : null;
  const matched =
    !!data &&
    envelope?.ok === true &&
    Array.isArray(envelope.errors) &&
    envelope.errors.length === 0 &&
    source.success &&
    data.siteId === args.siteId &&
    data.operationId === operation?.id &&
    data.operationVersion === operation.version &&
    data.connectionId === source.data.connectionId &&
    data.connectionVersion === source.data.connectionVersion &&
    data.sourceSiteVersion === source.data.siteVersion &&
    data.upstreamRevision === source.data.upstreamRevision;
  const ready = matched && !read.loading && !read.failed;
  const observedAt = data?.observedAt;
  const primary = data?.providers.find((row) => row.provider === selected)?.credentials;
  const primaryKey = primary?.identityName
    ? primary.identityNamespace + "/" + primary.identityName
    : null;
  const identityCount = primary?.additionalIdentityCount ?? 0;
  const validatePage = useMemo(
    () => (response: unknown, review: Record<string, unknown>) => {
      const envelope = (response as GetNvkeProviderIdentitiesQuery | undefined)
        ?.astroliftNvkeProviderIdentitiesPage;
      const parsed = nvkeProviderIdentityPageSchema.safeParse(envelope?.data);
      if (
        !parsed.success ||
        !envelope?.ok ||
        !Array.isArray(envelope.errors) ||
        envelope.errors.length
      )
        return false;
      const page = parsed.data;
      return (
        page.siteId === review.siteId &&
        page.operationId === review.operationId &&
        page.operationVersion === review.expectedVersion &&
        page.observedAt === observedAt &&
        page.provider === review.provider &&
        page.provider === selected &&
        page.items.length <= Number(review.limit) &&
        page.totalCount <= identityCount &&
        (!!review.search || page.totalCount === identityCount) &&
        page.items.every((row) => row.key !== primaryKey)
      );
    },
    [observedAt, selected, identityCount, primaryKey]
  );
  const identities = useScopedCursorTable<NvkeProviderIdentity>(
    {
      query: GET_NVKE_PROVIDER_IDENTITIES,
      variables: { ...variables, provider: selected },
      skip: !ready,
      searchVariable: "search",
      fetchPolicy: "no-cache",
      extract: (response) =>
        (response as GetNvkeProviderIdentitiesQuery | undefined)
          ?.astroliftNvkeProviderIdentitiesPage?.data,
    },
    ready ? JSON.stringify([key, selected, observedAt]) : "",
    { ...NVKE_REQUEST_CONTEXT, expectedNvkeOrganization: context.scope?.organizationId },
    validatePage
  );
  if (!eligible) return null;
  return {
    observation: ready ? data : null,
    provider: selected,
    identities,
    loading: source.success && read.loading,
    error: !source.success || read.failed || (!read.loading && !matched) ? t("readFailed") : null,
    onRefresh: () => {
      read.refresh();
      identities.refetch();
    },
    onProviderChange: (provider) => setSelection({ key, provider }),
  };
};
