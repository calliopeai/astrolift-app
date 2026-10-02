"use client";

import { useLazyQuery } from "@apollo/client/react";
import * as React from "react";

import type {
  GetPreviewDeploymentsPageQuery,
  GetPreviewLogsQuery,
} from "@/graphql/__generated__/operations";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import {
  GET_PREVIEW_DEPLOYMENTS_PAGE,
  GET_PREVIEW_ENVIRONMENT,
  GET_PREVIEW_LOGS,
} from "@/graphql/lifecycle/lifecycle.queries";
import type { AstroliftPreviewEnvironment } from "@/graphql/lifecycle/lifecycle.types";
import { useMe } from "@/graphql/user/user.hooks";
import { previewHasAvailableBinding } from "./preview-binding";

interface Resp {
  astroliftPreviewEnvironment: AstroliftPreviewEnvironment | null;
}

type Logs = GetPreviewLogsQuery["astroliftAppLogs"];
type Deployments = GetPreviewDeploymentsPageQuery["astroliftPreviewDeploymentsPage"];
type Snapshot<T> = { key: string; data: T | null; error: { message: string } | null };

/** Exact metadata lookup; resource/pricing and bound logs/history are explicit reads. */
export function usePreviewDetail(id: string) {
  const { org } = useActiveOrg();
  const { user } = useMe();
  const scopeKey = org && user ? `${org.id}:${user.id}:${id}` : "";
  const currentScope = React.useRef(scopeKey);
  React.useLayoutEffect(() => {
    currentScope.current = scopeKey;
  }, [scopeKey]);
  const metadataGeneration = React.useRef(0);
  const [metadata, setMetadata] = React.useState<Snapshot<AstroliftPreviewEnvironment> | null>(
    null
  );
  const [logs, setLogs] = React.useState<Snapshot<Logs> | null>(null);
  const [deployments, setDeployments] = React.useState<Snapshot<Deployments> | null>(null);
  const [getPreview, metadataQuery] = useLazyQuery<Resp>(GET_PREVIEW_ENVIRONMENT, {
    fetchPolicy: "no-cache",
  });
  const [getLogs, logsQuery] = useLazyQuery<GetPreviewLogsQuery>(GET_PREVIEW_LOGS, {
    fetchPolicy: "no-cache",
  });
  const [getDeployments, deploymentsQuery] = useLazyQuery<GetPreviewDeploymentsPageQuery>(
    GET_PREVIEW_DEPLOYMENTS_PAGE,
    { fetchPolicy: "no-cache" }
  );

  const loadMetadata = React.useCallback(
    async (includeRuntimeCost = false) => {
      if (!scopeKey || currentScope.current !== scopeKey) return;
      const generation = ++metadataGeneration.current;
      try {
        const result = await getPreview({
          variables: { id, includeRuntimeCost },
          context: { queryDeduplication: false },
        });
        if (currentScope.current !== scopeKey || generation !== metadataGeneration.current) return;
        const preview = result.data?.astroliftPreviewEnvironment;
        setMetadata({
          key: scopeKey,
          data: preview?.id === id ? preview : null,
          error: result.error ? { message: result.error.message } : null,
        });
      } catch (error) {
        if (currentScope.current === scopeKey && generation === metadataGeneration.current)
          setMetadata({
            key: scopeKey,
            data: null,
            error: { message: error instanceof Error ? error.message : "Request failed" },
          });
      }
    },
    [getPreview, id, scopeKey]
  );

  React.useEffect(() => {
    void Promise.resolve().then(() => loadMetadata());
    return () => {
      metadataGeneration.current += 1;
    };
  }, [loadMetadata]);

  const visibleMetadata = metadata?.key === scopeKey ? metadata : null;
  const preview = visibleMetadata?.error ? null : (visibleMetadata?.data ?? null);
  const target = preview && previewHasAvailableBinding(preview) ? preview.environment : null;
  const targetKey = target
    ? `${scopeKey}:${target.previewVersion}:${target.environmentId}:${target.environmentVersion}`
    : "";
  const currentTarget = React.useRef(targetKey);
  React.useLayoutEffect(() => {
    currentTarget.current = targetKey;
  }, [targetKey]);
  const proof = target
    ? {
        expectedEnvironmentId: target.environmentId,
        ifMatchPreviewVersion: target.previewVersion,
        ifMatchEnvironmentVersion: target.environmentVersion,
      }
    : null;

  const loadLogs = async () => {
    if (!target || !proof) return;
    const until = new Date();
    try {
      const result = await getLogs({
        context: { queryDeduplication: false },
        variables: {
          appSlug: target.appSlug,
          previewId: id,
          ...proof,
          since: new Date(until.getTime() - 30 * 60 * 1000).toISOString(),
          until: until.toISOString(),
          limit: 200,
        },
      });
      if (currentTarget.current !== targetKey) return;
      setLogs({
        key: targetKey,
        data: result.data?.astroliftAppLogs ?? null,
        error: result.error ? { message: result.error.message } : null,
      });
    } catch (error) {
      if (currentTarget.current === targetKey)
        setLogs({
          key: targetKey,
          data: null,
          error: { message: error instanceof Error ? error.message : "Request failed" },
        });
    }
  };

  const loadDeployments = async () => {
    if (!target || !proof) return;
    try {
      const result = await getDeployments({
        variables: { id, ...proof, limit: 20, after: null },
        context: { queryDeduplication: false },
      });
      if (currentTarget.current !== targetKey) return;
      setDeployments({
        key: targetKey,
        data: result.data?.astroliftPreviewDeploymentsPage ?? null,
        error: result.error ? { message: result.error.message } : null,
      });
    } catch (error) {
      if (currentTarget.current === targetKey)
        setDeployments({
          key: targetKey,
          data: null,
          error: { message: error instanceof Error ? error.message : "Request failed" },
        });
    }
  };

  const visibleLogs = logs?.key === targetKey && targetKey ? logs : null;
  const visibleDeployments = deployments?.key === targetKey && targetKey ? deployments : null;
  return {
    id,
    preview,
    loading: Boolean(scopeKey && !visibleMetadata) || (metadataQuery.loading && !preview),
    error: visibleMetadata?.error ?? null,
    onRetry: () => {
      void loadMetadata();
    },
    runtimeLoading: metadataQuery.loading && Boolean(preview),
    onLoadRuntime: () => {
      if (target) void loadMetadata(true);
    },
    logs: visibleLogs?.data ?? null,
    logsError: visibleLogs?.error ?? null,
    logsLoading: logsQuery.loading,
    logsRequested: Boolean(visibleLogs),
    onLoadLogs: () => {
      void loadLogs();
    },
    deployments: visibleDeployments?.data ?? null,
    deploymentsError: visibleDeployments?.error ?? null,
    deploymentsLoading: deploymentsQuery.loading,
    deploymentsRequested: Boolean(visibleDeployments),
    onLoadDeployments: () => {
      void loadDeployments();
    },
  };
}
