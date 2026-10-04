"use client";

import { useLayoutEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { useQuery } from "@apollo/client/react";
import { GET_MODEL_CONNECTION_CAPABILITIES } from "@/graphql/models/model-connections.queries";
import type { GetModelConnectionCapabilitiesQuery } from "@/graphql/__generated__/operations";

export function useModelConnectionSupport() {
  const query = useQuery<GetModelConnectionCapabilitiesQuery>(GET_MODEL_CONNECTION_CAPABILITIES, {
    fetchPolicy: "no-cache",
    context: { queryDeduplication: false },
  });
  const t = useTranslations("models.shared.connections");
  const capabilities = query.data?.astroliftServerInfo?.capabilities;
  const valid =
    Array.isArray(capabilities) && capabilities.every((value) => typeof value === "string");
  return {
    supported:
      query.loading || query.error || !valid
        ? null
        : capabilities.includes("models.connection_approvals"),
    error:
      query.error?.message ??
      (!query.loading && query.data && !valid ? t("capabilityFailed") : null),
    onRetry: () => {
      void query.refetch().catch(() => {});
    },
  };
}

export function useConnectionEpoch(binding: unknown) {
  const key = JSON.stringify(binding);
  const [scope, setScope] = useState({ key, revision: 0 });
  if (scope.key !== key) setScope({ key, revision: scope.revision + 1 });
  const latest = useRef({ key, revision: scope.revision }),
    lifecycle = useRef(0),
    mounted = useRef(false);
  useLayoutEffect(() => {
    latest.current = { key, revision: scope.revision };
  });
  useLayoutEffect(() => {
    mounted.current = true;
    const epoch = lifecycle.current + 1;
    lifecycle.current = epoch;
    return () => {
      mounted.current = false;
      lifecycle.current = epoch + 1;
    };
  }, []);
  return () => {
    const epoch = lifecycle.current;
    return () =>
      mounted.current &&
      lifecycle.current === epoch &&
      latest.current.key === key &&
      latest.current.revision === scope.revision;
  };
}
