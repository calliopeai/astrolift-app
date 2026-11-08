"use client";

import { useTranslations } from "next-intl";
import { useMemo } from "react";
import { useScopedCursorTable } from "@/components/data-table/use-scoped-cursor-table";
import type {
  NvkeCredentialCertificate,
  NvkeCredentialMetric,
  NvkeCredentialPanelProps,
} from "@/components/screens/nvke/NvkeCredentialPanel";
import type { NvkeOperationView } from "@/components/screens/nvke/types";
import type {
  GetNvkeCredentialObservationQuery,
  GetNvkeCredentialCertificatesQuery,
  GetNvkeCredentialMetricsQuery,
} from "@/graphql/__generated__/operations";
import { validNvkeGuid } from "@/lib/nvke/recovery";
import type { useNvkeContext } from "./nvke.hooks";
import { NVKE_REQUEST_CONTEXT, useNvkeRead } from "./use-scoped-read";
import {
  GET_NVKE_CREDENTIAL_OBSERVATION,
  GET_NVKE_CREDENTIAL_CERTIFICATES,
  GET_NVKE_CREDENTIAL_METRICS,
} from "./nvke-metrics.queries";

const object = (value: unknown): Record<string, unknown> | null =>
  value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
const matches = (data: Record<string, unknown> | null, variables: Record<string, unknown>) =>
  !!data &&
  data.siteId === variables.siteId &&
  data.operationId === variables.operationId &&
  data.operationVersion === variables.expectedVersion &&
  typeof data.scrapedAt === "string" &&
  Number.isFinite(Date.parse(data.scrapedAt));
const nullableText = (value: unknown) => value == null || typeof value === "string";
const nullableBool = (value: unknown) => value == null || typeof value === "boolean";
const validItem = (value: unknown, certificates: boolean) => {
  const row = object(value);
  if (!row || typeof row.key !== "string" || !row.key) return false;
  if (!certificates)
    return (
      typeof row.metric === "string" &&
      !!row.metric &&
      typeof row.value === "string" &&
      !!row.value &&
      nullableText(row.node) &&
      nullableText(row.credential) &&
      nullableText(row.state)
    );
  return (
    typeof row.node === "string" &&
    !!row.node &&
    typeof row.credential === "string" &&
    !!row.credential &&
    nullableText(row.observationAgeSeconds) &&
    nullableText(row.reportedNotAfter) &&
    [
      row.required,
      row.observationAvailable,
      row.latestInspectionAvailable,
      row.reportedExpiredAtScrape,
    ].every(nullableBool)
  );
};
const pageValidator =
  (field: string, scrapedAt: string | undefined) =>
  (response: unknown, variables: Record<string, unknown>) => {
    const envelope = object(object(response)?.[field]),
      data = object(envelope?.data);
    return (
      envelope?.ok === true &&
      Array.isArray(envelope.errors) &&
      envelope.errors.length === 0 &&
      matches(data, variables) &&
      data?.scrapedAt === scrapedAt &&
      Array.isArray(data?.items) &&
      data.items.every((item) => validItem(item, field.includes("Certificates"))) &&
      new Set(data.items.map((item) => object(item)?.key)).size === data.items.length &&
      data.items.length <= Number(variables.limit) &&
      Number.isSafeInteger(data.totalCount) &&
      Number(data.totalCount) >= data.items.length &&
      (data.nextCursor === null || typeof data.nextCursor === "string")
    );
  };

export const useNvkeCredentials = (args: {
  context: ReturnType<typeof useNvkeContext>;
  scopeKey: string;
  siteId: string;
  operation: NvkeOperationView | null;
  enabled: boolean;
}): NvkeCredentialPanelProps | null => {
  const { context, operation } = args,
    t = useTranslations("nvke");
  const eligible =
    args.enabled &&
    !!context.scope &&
    !!args.scopeKey &&
    !context.failed &&
    !context.loading &&
    !context.access.loading &&
    context.access.read &&
    operation?.siteId === args.siteId &&
    operation.operationKey === "metrics.credentials" &&
    operation.status === "SUCCEEDED" &&
    !operation.isInventory;
  const variables = {
    siteId: args.siteId,
    operationId: operation?.id,
    expectedActorId: context.scope?.actorId,
    expectedVersion: operation?.version,
  };
  const key = eligible
    ? JSON.stringify([args.scopeKey, args.siteId, operation.id, operation.version])
    : "";
  const read = useNvkeRead<GetNvkeCredentialObservationQuery>(
    GET_NVKE_CREDENTIAL_OBSERVATION,
    variables,
    key,
    0,
    context.scope?.organizationId
  );
  const envelope = read.data?.astroliftNvkeCredentialObservation,
    data = envelope?.data;
  const matched =
    !!data &&
    envelope?.ok === true &&
    Array.isArray(envelope.errors) &&
    envelope.errors.length === 0 &&
    matches(object(data), variables) &&
    validNvkeGuid(data.clusterId) &&
    validNvkeGuid(data.sourceOperationId) &&
    validNvkeGuid(data.authorityId) &&
    Number.isSafeInteger(data.sourceVersion) &&
    data.sourceVersion > 0 &&
    Number.isSafeInteger(data.authorityVersion) &&
    data.authorityVersion > 0 &&
    Number.isSafeInteger(data.sourceSiteVersion) &&
    data.sourceSiteVersion > 0 &&
    [data.collectionSucceeded, data.monitoringEnabled, data.renewalAutomationEnabled].every(
      nullableBool
    ) &&
    nullableText(data.warningWindowSeconds) &&
    typeof data.clusterReported === "boolean";
  const pageKey = matched && !read.failed && !read.loading ? key : "";
  const requestContext = {
    ...NVKE_REQUEST_CONTEXT,
    expectedNvkeOrganization: context.scope?.organizationId,
  };
  const validateCertificates = useMemo(
    () => pageValidator("astroliftNvkeCredentialCertificatesPage", data?.scrapedAt),
    [data?.scrapedAt]
  );
  const validateSamples = useMemo(
    () => pageValidator("astroliftNvkeCredentialMetricsPage", data?.scrapedAt),
    [data?.scrapedAt]
  );
  const certificates = useScopedCursorTable<NvkeCredentialCertificate>(
    {
      query: GET_NVKE_CREDENTIAL_CERTIFICATES,
      variables,
      skip: !pageKey,
      searchVariable: "search",
      fetchPolicy: "no-cache",
      extract: (response) =>
        (response as GetNvkeCredentialCertificatesQuery | undefined)
          ?.astroliftNvkeCredentialCertificatesPage?.data,
    },
    pageKey,
    requestContext,
    validateCertificates
  );
  const samples = useScopedCursorTable<NvkeCredentialMetric>(
    {
      query: GET_NVKE_CREDENTIAL_METRICS,
      variables,
      skip: !pageKey,
      searchVariable: "search",
      fetchPolicy: "no-cache",
      extract: (response) =>
        (response as GetNvkeCredentialMetricsQuery | undefined)?.astroliftNvkeCredentialMetricsPage
          ?.data,
    },
    pageKey,
    requestContext,
    validateSamples
  );
  if (!eligible) return null;
  return {
    observation: matched && !read.failed && !read.loading ? data : null,
    certificates,
    samples,
    loading: read.loading,
    error: read.failed || (!read.loading && !matched) ? t("readFailed") : null,
    onRefresh: () => {
      read.refresh();
      certificates.refetch();
      samples.refetch();
    },
  };
};
