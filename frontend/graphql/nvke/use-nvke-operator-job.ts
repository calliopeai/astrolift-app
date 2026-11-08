"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useApolloClient } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import { useScopedCursorTable } from "@/components/data-table/use-scoped-cursor-table";
import type { NvkeOperatorJobPanelProps } from "@/components/screens/nvke/NvkeOperatorJobPanel";
import type { NvkeSiteView, NvkeOperationView } from "@/components/screens/nvke/types";
import type {
  GetNvkeOperatorAuthorityQuery,
  GetNvkeOperatorJobPageQuery,
  GetNvkeOperatorArtifactTextQuery,
  ReconcileNvkeOperatorJobMutation,
  ReconcileNvkeOperatorJobMutationVariables,
} from "@/graphql/__generated__/operations";
import { operatorJobPageSchema } from "@/lib/nvke/operator-job";
import {
  downloadNvkeOperatorArtifact,
  operatorArtifactFilename,
  NvkeOperatorArtifactError,
  type NvkeOperatorArtifact,
} from "@/lib/nvke/operator-artifact";
import { validNvkeGuid } from "@/lib/nvke/recovery";
import { useNvkeEpoch, type useNvkeContext } from "./nvke.hooks";
import { NVKE_REQUEST_CONTEXT, useNvkeRead } from "./use-scoped-read";
import {
  GET_NVKE_OPERATOR_AUTHORITY,
  GET_NVKE_OPERATOR_JOB_PAGE,
  GET_NVKE_OPERATOR_ARTIFACT_TEXT,
} from "./nvke-operator.queries";
import { RECONCILE_NVKE_OPERATOR_JOB } from "./nvke-operator.mutations";
import { operatorAuthorityMetadata } from "./use-nvke-operator-agent";

export const useNvkeOperatorJob = (args: {
  context: ReturnType<typeof useNvkeContext>;
  scopeKey: string;
  site: NvkeSiteView | null;
  operation: NvkeOperationView | null;
  enabled: boolean;
  onRefresh: () => void;
}): NvkeOperatorJobPanelProps | null => {
  const { context, site, operation } = args,
    client = useApolloClient(),
    t = useTranslations("nvke");
  const [refreshEpoch, setRefreshEpoch] = useState(0),
    [activated, setActivated] = useState("");
  const eligible =
    args.enabled &&
    !!site &&
    !!operation &&
    operation.siteId === site.id &&
    operation.operationKey.startsWith("tooling.") &&
    !!context.scope &&
    !!args.scopeKey &&
    !context.failed &&
    !context.loading &&
    !context.access.loading &&
    context.access.read;
  const { key: visit } = useNvkeEpoch(
    eligible
      ? JSON.stringify([
          args.scopeKey,
          context.scope,
          context.access.admin,
          site.id,
          site.version,
          site.upstreamRevision,
          site.isActive,
          operation.id,
          operation.version,
          operation.status,
          operation.requestId,
          refreshEpoch,
        ])
      : ""
  );
  const authorityRead = useNvkeRead<GetNvkeOperatorAuthorityQuery>(
    GET_NVKE_OPERATOR_AUTHORITY,
    { siteId: site?.id },
    eligible && context.access.admin ? visit : "",
    0,
    context.scope?.organizationId
  );
  const rawAuthority = authorityRead.data?.astroliftNvkeOperatorAuthority;
  const authority = useMemo(() => operatorAuthorityMetadata(rawAuthority), [rawAuthority]);
  const { key, isCurrent: current } = useNvkeEpoch(
    eligible ? JSON.stringify([visit, authority]) : ""
  );
  const sourceReady =
    eligible &&
    context.access.admin &&
    !authorityRead.loading &&
    !authorityRead.failed &&
    site?.isActive === true &&
    validNvkeGuid(site.id) &&
    Number.isSafeInteger(site.version) &&
    site.version > 0 &&
    !!operation &&
    validNvkeGuid(operation.id) &&
    validNvkeGuid(operation.requestId) &&
    Number.isSafeInteger(operation.version) &&
    operation.version > 0 &&
    !!authority &&
    validNvkeGuid(authority.id) &&
    validNvkeGuid(authority.instanceId) &&
    authority.siteId === site.id &&
    authority.siteVersion === site.version &&
    Number.isSafeInteger(authority.version) &&
    authority.version > 0 &&
    /^[0-9a-f]{64}$/.test(authority.bundleManifestSha256) &&
    authority.isEnabled === true &&
    authority.deletedAt === null &&
    authority.hasCredentials === true &&
    authority.verificationCode === "AGENT_VERIFIED" &&
    typeof authority.verifiedAt === "string" &&
    Number.isFinite(Date.parse(authority.verifiedAt));
  const active = activated === key && sourceReady;
  const input = {
    operationId: operation?.id,
    expectedActorId: context.scope?.actorId,
    expectedSiteVersion: site?.version,
    expectedAuthorityVersion: authority?.version,
    expectedOperationVersion: operation?.version,
  };
  const requestContext = {
    ...NVKE_REQUEST_CONTEXT,
    expectedNvkeOrganization: context.scope?.organizationId,
  };
  const summaryRead = useNvkeRead<GetNvkeOperatorJobPageQuery>(
    GET_NVKE_OPERATOR_JOB_PAGE,
    { input, limit: 1 },
    active ? key : "",
    0,
    context.scope?.organizationId
  );
  const parsed = useMemo(() => {
    const result = summaryRead.data?.astroliftNvkeOperatorJobPage;
    return result?.ok === true && Array.isArray(result.errors) && result.errors.length === 0
      ? operatorJobPageSchema.safeParse(result.data)
      : null;
  }, [summaryRead.data]);
  const summary = parsed?.success ? parsed.data : null;
  const sourceIdentity = JSON.stringify([
    site?.id,
    site?.version,
    operation?.id,
    operation?.version,
    authority?.id,
    authority?.version,
    authority?.instanceId,
    authority?.bundleManifestSha256,
    site?.upstreamRevision,
    operation?.operationKey,
  ]);
  const matches = useCallback(
    (page: unknown) => {
      const parsed = operatorJobPageSchema.safeParse(page);
      if (!parsed.success) return false;
      const value = parsed.data;
      return (
        JSON.stringify([
          value.siteId,
          value.siteVersion,
          value.operationId,
          value.operationVersion,
          value.authorityId,
          value.authorityVersion,
          value.instanceId,
          value.bundleManifestSha256,
          value.nativeRevision,
          `tooling.${value.command}`,
        ]) === sourceIdentity
      );
    },
    [sourceIdentity]
  );
  const matched = !!summary && matches(summary);
  const validatePage = useCallback(
    (response: unknown) => {
      const envelope = (response as GetNvkeOperatorJobPageQuery | undefined)
        ?.astroliftNvkeOperatorJobPage;
      return (
        envelope?.ok === true &&
        Array.isArray(envelope.errors) &&
        envelope.errors.length === 0 &&
        matches(envelope.data) &&
        envelope.data?.receiptSha256 === summary?.receiptSha256
      );
    },
    [matches, summary?.receiptSha256]
  );
  const table = useScopedCursorTable<NvkeOperatorArtifact>(
    {
      query: GET_NVKE_OPERATOR_JOB_PAGE,
      variables: { input },
      extract: (response) => {
        const parsed = operatorJobPageSchema.safeParse(
          (response as GetNvkeOperatorJobPageQuery | undefined)?.astroliftNvkeOperatorJobPage.data
        );
        return parsed.success ? parsed.data : undefined;
      },
      skip: !active || !matched,
      searchVariable: "search",
    },
    active && matched ? key : "",
    requestContext,
    validatePage
  );
  const [mutation, setMutation] = useState<{
    key: string;
    pending: boolean;
    error: string | null;
    progress: { completed: number; total: number } | null;
  } | null>(null);
  const busy = useRef(false),
    blockedEpoch = useRef(""),
    download = useRef<AbortController | null>(null);
  useEffect(
    () => () => {
      download.current?.abort();
    },
    [key]
  );
  const onRefresh = () => {
    if (!current() || busy.current) return;
    setMutation(null);
    setActivated("");
    setRefreshEpoch((value) => value + 1);
    args.onRefresh();
  };
  if (!eligible || !site || !operation) return null;
  const {
    items: _items,
    nextCursor: _nextCursor,
    totalCount: _totalCount,
    ...receipt
  } = summary ?? {};
  return {
    scopeKey: key,
    site,
    operation,
    authority: sourceReady
      ? {
          id: authority!.id,
          version: authority!.version,
          siteId: authority!.siteId,
          siteVersion: authority!.siteVersion,
          endpoint: authority!.endpoint,
          instanceId: authority!.instanceId,
          bundleManifestSha256: authority!.bundleManifestSha256,
          isEnabled: authority!.isEnabled,
          timeoutSeconds: authority!.timeoutSeconds,
          hasCredentials: authority!.hasCredentials,
          verificationCode: authority!.verificationCode,
          verifiedAt: authority!.verifiedAt ?? null,
          deletedAt: authority!.deletedAt ?? null,
        }
      : null,
    receipt: active && matched ? (receipt as NvkeOperatorJobPanelProps["receipt"]) : null,
    table:
      table.state === "error"
        ? { ...table, rows: [], totalCount: null, nextCursor: null, isStale: false }
        : table,
    loaded: active,
    loading: authorityRead.loading || (active && summaryRead.loading),
    canManage: sourceReady,
    canReconcile:
      sourceReady &&
      active &&
      matched &&
      operation.status === "UNCERTAIN" &&
      !(mutation?.key === key && mutation.error),
    pending: mutation?.key === key && mutation.pending,
    progress: mutation?.key === key ? mutation.progress : null,
    error:
      mutation?.key === key && mutation.error
        ? mutation.error
        : authorityRead.failed ||
            (active &&
              (summaryRead.failed || (!summaryRead.loading && !matched) || table.state === "error"))
          ? t("readFailed")
          : null,
    onLoad: () => {
      if (current() && sourceReady && !busy.current) setActivated(key);
    },
    onRefresh,
    onCancelDownload: () => {
      if (current()) download.current?.abort();
    },
    onReconcile: async () => {
      if (
        !current() ||
        !sourceReady ||
        !active ||
        !matched ||
        operation.status !== "UNCERTAIN" ||
        busy.current ||
        blockedEpoch.current === key
      )
        return false;
      busy.current = true;
      setMutation({ key, pending: true, error: null, progress: null });
      try {
        const response = await client.mutate<
          ReconcileNvkeOperatorJobMutation,
          ReconcileNvkeOperatorJobMutationVariables
        >({
          mutation: RECONCILE_NVKE_OPERATOR_JOB,
          variables: {
            input: {
              operationId: operation.id,
              expectedActorId: context.scope!.actorId,
              expectedSiteVersion: site.version,
              expectedAuthorityVersion: authority!.version,
              expectedOperationVersion: operation.version,
            },
          },
          fetchPolicy: "no-cache",
          context: requestContext,
        });
        if (!current()) return false;
        const reply = response.data?.reconcileNvkeOperatorJob,
          row = reply?.data;
        if (
          reply?.ok !== true ||
          !Array.isArray(reply.errors) ||
          reply.errors.length ||
          !row ||
          row.id !== operation.id ||
          row.siteId !== site.id ||
          row.requestId !== operation.requestId ||
          row.operationKey !== operation.operationKey ||
          row.version <= operation.version ||
          !["SUCCEEDED", "FAILED", "CANCELLED", "UNCERTAIN"].includes(row.status)
        )
          throw new Error("UNCONFIRMED");
        setMutation({ key, pending: false, error: null, progress: null });
        setActivated("");
        args.onRefresh();
        setRefreshEpoch((value) => value + 1);
        return true;
      } catch {
        if (current()) {
          blockedEpoch.current = key;
          setMutation({ key, pending: false, error: t("requestFailed"), progress: null });
        }
        return false;
      } finally {
        busy.current = false;
      }
    },
    onDownload: async (requestedArtifact, maximumBytes) => {
      const artifact = { ...requestedArtifact };
      if (
        !current() ||
        !sourceReady ||
        !active ||
        !matched ||
        !summary.found ||
        busy.current ||
        table.state !== "ready" ||
        !table.rows.some(
          (row) =>
            row.name === artifact.name &&
            row.size === artifact.size &&
            row.sha256 === artifact.sha256
        )
      )
        return false;
      const controller = new AbortController();
      download.current = controller;
      busy.current = true;
      setMutation({
        key,
        pending: true,
        error: null,
        progress: { completed: 0, total: Number(artifact.size) },
      });
      try {
        const blob = await downloadNvkeOperatorArtifact({
          operationId: operation.id,
          artifact,
          maximumBytes,
          current: () => current() && !controller.signal.aborted,
          onProgress: (completed, total) => {
            if (current())
              setMutation({ key, pending: true, error: null, progress: { completed, total } });
          },
          readChunk: async (offset, limit) => {
            const response = await client.query<GetNvkeOperatorArtifactTextQuery>({
              query: GET_NVKE_OPERATOR_ARTIFACT_TEXT,
              variables: { input: { ...input, name: artifact.name, offset, limit } },
              fetchPolicy: "no-cache",
              context: {
                ...requestContext,
                fetchOptions: { ...NVKE_REQUEST_CONTEXT.fetchOptions, signal: controller.signal },
              },
            });
            const envelope = response.data?.astroliftNvkeOperatorArtifactText;
            if (
              response.error ||
              envelope?.ok !== true ||
              !Array.isArray(envelope.errors) ||
              envelope.errors.length
            )
              throw new Error("DOWNLOAD_FAILED");
            return envelope.data;
          },
        });
        if (!current() || controller.signal.aborted) return false;
        const url = URL.createObjectURL(blob);
        try {
          const anchor = document.createElement("a");
          anchor.href = url;
          anchor.download = operatorArtifactFilename(operation.id, artifact.name);
          anchor.click();
        } finally {
          setTimeout(() => URL.revokeObjectURL(url), 1000);
        }
        setMutation({ key, pending: false, error: null, progress: null });
        return true;
      } catch (error) {
        if (current())
          setMutation({
            key,
            pending: false,
            error: controller.signal.aborted
              ? null
              : error instanceof NvkeOperatorArtifactError && error.code === "TOO_LARGE"
                ? t("operatorJob.tooLarge")
                : t("downloadFailed"),
            progress: null,
          });
        return false;
      } finally {
        download.current = null;
        busy.current = false;
      }
    },
  };
};
