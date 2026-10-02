"use client";

import { useLazyQuery } from "@apollo/client/react";
import * as React from "react";
import type { AstroliftManagedServiceMetrics } from "@/graphql/__generated__/schema";
import { GET_MANAGED_SERVICE_METRICS } from "@/graphql/observability/observability.queries";
import { useMe } from "@/graphql/user/user.hooks";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import { TIME_RANGE_OPTIONS, type TimeRangeKey } from "./golden-signals-types";

interface MetricsResp {
  astroliftAppManagedServiceMetrics: AstroliftManagedServiceMetrics | null;
}
export interface ManagedServiceMetricsState {
  range: TimeRangeKey;
  onRangeChange: (range: TimeRangeKey) => void;
  data: AstroliftManagedServiceMetrics | null;
  loading: boolean;
  error?: boolean;
}

/** Observe runtime samples only for this actor/org/reviewed context and range. */
export function useManagedServiceMetrics(
  managedServiceId: string,
  expectedContextRevision?: string
): ManagedServiceMetricsState {
  const { user } = useMe();
  const { org } = useActiveOrg();
  const orgId = org?.id;
  const binding =
    user && orgId && managedServiceId
      ? `${user.id}:${orgId}:${managedServiceId}:${expectedContextRevision ?? "legacy"}`
      : "";
  const [scope, setScope] = React.useState({ binding, epoch: 0 });
  const [range, setRange] = React.useState<TimeRangeKey>("1h");
  const [snapshot, setSnapshot] = React.useState<{
    key: string;
    data: AstroliftManagedServiceMetrics | null;
    error: boolean;
  } | null>(null);
  if (scope.binding !== binding) {
    setScope({ binding, epoch: scope.epoch + 1 });
    setSnapshot(null);
  }
  const rangeSeconds = TIME_RANGE_OPTIONS.find((option) => option.key === range)?.seconds ?? 3600;
  const identityKey = scope.binding === binding && binding ? `${binding}:${scope.epoch}` : "";
  const key = identityKey ? `${identityKey}:${rangeSeconds}` : "";
  const latest = React.useRef({ identityKey, key, generation: 0 });
  React.useLayoutEffect(() => {
    latest.current = { identityKey, key, generation: latest.current.generation + 1 };
  }, [identityKey, key]);
  const [execute] = useLazyQuery<MetricsResp>(GET_MANAGED_SERVICE_METRICS, {
    fetchPolicy: "no-cache",
  });
  React.useEffect(() => {
    if (!key) return;
    const request = ++latest.current.generation;
    let disposed = false;
    const finish = (data: AstroliftManagedServiceMetrics | null, error: boolean) => {
      if (!disposed && latest.current.key === key && latest.current.generation === request)
        setSnapshot({ key, data, error });
    };
    void execute({
      variables: {
        managedServiceId,
        rangeSeconds,
        ...(expectedContextRevision !== undefined ? { expectedContextRevision } : {}),
      },
      context: { queryDeduplication: false },
    }).then(
      ({ data, error }) => {
        const row = data?.astroliftAppManagedServiceMetrics;
        const valid =
          row?.managedServiceId === managedServiceId && row.rangeSeconds === rangeSeconds;
        finish(!error && valid ? row : null, Boolean(error) || (Boolean(row) && !valid));
      },
      () => finish(null, true)
    );
    return () => {
      disposed = true;
    };
  }, [key, managedServiceId, rangeSeconds, expectedContextRevision, execute]);
  const observed = Boolean(key) && snapshot?.key === key;
  return {
    range,
    onRangeChange: (value) => {
      if (identityKey && latest.current.identityKey === identityKey) setRange(value);
    },
    data: observed ? snapshot.data : null,
    loading: Boolean(key) && !observed,
    error: !identityKey || (observed && snapshot.error),
  };
}
