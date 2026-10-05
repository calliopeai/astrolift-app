"use client";
import * as React from "react";
import { useApolloClient, useQuery } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import type {
  DomainAppRouteSupportQuery,
  DomainAppRouteTargetsQuery,
  DomainAppRouteReviewQuery,
  DomainAppRouteOperationQuery,
  DomainAppRouteOperationFieldsFragment as Operation,
  PrepareDomainAppRouteMutation,
  ConnectDomainAppRouteMutation,
  ReconcileDomainAppRouteMutation,
} from "@/graphql/__generated__/domain-app-route";
import {
  DOMAIN_APP_ROUTE_SUPPORT,
  DOMAIN_APP_ROUTE_TARGETS,
  DOMAIN_APP_ROUTE_REVIEW,
  DOMAIN_APP_ROUTE_OPERATION,
  PREPARE_DOMAIN_APP_ROUTE,
  CONNECT_DOMAIN_APP_ROUTE,
  RECONCILE_DOMAIN_APP_ROUTE,
} from "@/graphql/domains/domain-app-route.queries";
import type { DomainAppConnectionSheetProps, RouteReviewView } from "./DomainAppConnectionSheet";
import {
  correlatedRouteOperation,
  routeGuid,
  routeHostname,
  routeTargetKey,
  routeVersion,
  validRouteIntent,
  validRouteReview,
  validRouteTarget,
  type RouteIntent,
  type RouteReview,
} from "./domain-app-route-contract";

export type DomainAppRouteContext = {
  actor: string;
  organization: string;
  domainId: string;
  domainVersion: number;
  zone: string;
  metadataCurrent: boolean;
  onReload: () => Promise<boolean>;
};
/** Private adapter. The actual backend must be composed before a public route mounts it. */
export function useDomainAppRoute(context: DomainAppRouteContext): DomainAppConnectionSheetProps {
  const client = useApolloClient(),
    d = useTranslations("managedDomains"),
    t = useTranslations("domainAppRouting");
  const [open, setOpen] = React.useState(false),
    [kind, setKind] = React.useState<"apex" | "subdomain">("apex"),
    [subdomain, setSubdomain] = React.useState("");
  const [cursors, setCursors] = React.useState<(string | null)[]>([null]),
    [targetKey, setTarget] = React.useState<string | null>(null),
    [review, setReview] = React.useState<RouteReview | null>(null);
  const [reviewing, setReviewing] = React.useState(false),
    [error, setError] = React.useState<string | null>(null),
    [busy, setBusy] = React.useState(false),
    [loaded, setLoaded] = React.useState(false),
    [storageBlocked, setStorageBlocked] = React.useState(false);
  const [intent, setIntent] = React.useState<RouteIntent | null>(null),
    [operation, setOperation] = React.useState<Operation | null>(null);
  const live = React.useRef(true),
    working = React.useRef(false),
    epoch = React.useRef(0),
    operationEpoch = React.useRef(0);
  const recoveryKey = `astrolift.domain-route.v1:${context.actor}:${context.organization}:${context.domainId}`;
  const admitted =
    context.metadataCurrent &&
    routeGuid(context.actor) &&
    routeGuid(context.organization) &&
    routeGuid(context.domainId) &&
    routeVersion(context.domainVersion);
  const variables = { domainId: context.domainId, expectedDomainVersion: context.domainVersion };
  const q = useQuery<DomainAppRouteSupportQuery>(DOMAIN_APP_ROUTE_SUPPORT, {
    variables,
    skip: !open || !admitted,
    fetchPolicy: "no-cache",
    context: { queryDeduplication: false },
    notifyOnNetworkStatusChange: true,
  });
  const support = admitted && !q.loading && !q.error ? q.data?.managedDomainAppRouteSupport : null;
  const allowed =
    support?.domainId === context.domainId &&
    support.version === context.domainVersion &&
    support.allowed === true &&
    ["ROUTE53_ALIAS", "MANUAL"].includes(support.writer);
  const p = useQuery<DomainAppRouteTargetsQuery>(DOMAIN_APP_ROUTE_TARGETS, {
    variables: { ...variables, after: cursors.at(-1) ?? null },
    skip: !open || !allowed || !!intent || !loaded,
    fetchPolicy: "no-cache",
    context: { queryDeduplication: false },
    notifyOnNetworkStatusChange: true,
  });
  const page = allowed && !p.loading && !p.error ? p.data?.managedDomainAppTargetsPage : null;
  const pageValid =
    !!page &&
    Array.isArray(page.items) &&
    page.items.every(validRouteTarget) &&
    new Set(page.items.map(routeTargetKey)).size === page.items.length &&
    typeof page.hasNextPage === "boolean" &&
    (!page.hasNextPage ||
      (typeof page.nextCursor === "string" &&
        page.nextCursor.length > 0 &&
        !cursors.includes(page.nextCursor)));
  const targets = pageValid ? page.items : [];
  const selected = targets.find((t) => routeTargetKey(t) === targetKey);
  const hostname = routeHostname(context.zone, kind, subdomain);
  const invalidate = React.useCallback(() => {
    ++epoch.current;
    setReview(null);
    setError(null);
    setReviewing(false);
  }, []);
  React.useLayoutEffect(() => {
    live.current = true;
    const reviewFence = epoch,
      operationFence = operationEpoch;
    return () => {
      live.current = false;
      ++reviewFence.current;
      ++operationFence.current;
    };
  }, []);
  React.useEffect(() => {
    queueMicrotask(() => {
      if (!live.current) return;
      try {
        const raw = sessionStorage.getItem(recoveryKey);
        if (raw) {
          const saved = JSON.parse(raw) as RouteIntent;
          if (!validRouteIntent(saved, context.domainId)) throw new Error();
          setIntent(saved);
        }
      } catch {
        setStorageBlocked(true);
      }
      setLoaded(true);
    });
  }, [recoveryKey, context.domainId]);
  React.useLayoutEffect(() => {
    const changed = ++epoch.current;
    queueMicrotask(() => {
      if (live.current && changed === epoch.current) {
        setReview(null);
        setError(null);
        setReviewing(false);
      }
    });
  }, [
    context.domainVersion,
    context.metadataCurrent,
    cursors,
    kind,
    subdomain,
    targetKey,
    q.loading,
    p.loading,
    q.error,
    p.error,
    invalidate,
  ]);
  React.useEffect(() => {
    const reset = () => invalidate();
    window.addEventListener("focus", reset);
    return () => window.removeEventListener("focus", reset);
  }, [invalidate]);
  const reviewed = !!(
    admitted &&
    allowed &&
    pageValid &&
    selected &&
    hostname &&
    review &&
    !reviewing &&
    !error &&
    validRouteReview(review, selected, context.domainId, context.domainVersion, hostname)
  );
  const locked = !!intent || storageBlocked || !loaded || busy;
  async function refresh() {
    if (working.current) return;
    invalidate();
    setTarget(null);
    setCursors([null]);
    try {
      if (!(await context.onReload())) throw new Error();
      await q.refetch();
    } catch {
      if (live.current) setError(d("refreshFailed"));
    }
  }
  async function onReview() {
    if (working.current || locked || !admitted || !allowed || !selected || !hostname || !pageValid)
      return;
    invalidate();
    const captured = epoch.current;
    setReviewing(true);
    try {
      const result = await client.query<DomainAppRouteReviewQuery>({
        query: DOMAIN_APP_ROUTE_REVIEW,
        variables: {
          input: {
            ...variables,
            environmentId: selected.environmentId,
            expectedEnvironmentVersion: selected.environmentVersion,
            workloadId: selected.workloadId,
            hostname,
          },
        },
        fetchPolicy: "no-cache",
        context: { queryDeduplication: false },
      });
      if (!live.current || captured !== epoch.current) return;
      const row = result.data?.managedDomainAppRouteReview;
      if (
        !row ||
        !validRouteReview(row, selected, context.domainId, context.domainVersion, hostname)
      )
        throw new Error();
      setReview(row);
    } catch {
      if (live.current && captured === epoch.current) setError(d("readFailed"));
    } finally {
      if (live.current && captured === epoch.current) setReviewing(false);
    }
  }
  async function recover(saved = intent) {
    if (
      !saved ||
      working.current ||
      !routeGuid(context.actor) ||
      !routeGuid(context.organization) ||
      !routeGuid(context.domainId)
    )
      return;
    working.current = true;
    setBusy(true);
    invalidate();
    const captured = operationEpoch.current;
    try {
      const result = await client.query<DomainAppRouteOperationQuery>({
        query: DOMAIN_APP_ROUTE_OPERATION,
        variables: { input: { domainId: context.domainId, requestId: saved.input.requestId } },
        fetchPolicy: "no-cache",
        context: { queryDeduplication: false },
      });
      if (!live.current || captured !== operationEpoch.current) return;
      const row = result.data?.managedDomainAppRouteOperation;
      if (!row || !correlatedRouteOperation(row, saved)) throw new Error();
      setOperation(row);
      setError(null);
    } catch {
      if (live.current && captured === operationEpoch.current) {
        setOperation(null);
        setError(d("readFailed"));
      }
    } finally {
      if (live.current) {
        working.current = false;
        setBusy(false);
      }
    }
  }
  async function send(purpose: "prepare" | "connect") {
    if (
      working.current ||
      locked ||
      !reviewed ||
      !review ||
      !selected ||
      !hostname ||
      review.writer !== "ROUTE53_ALIAS" ||
      !(purpose === "prepare" ? review.canPrepare : review.canAttach)
    )
      return;
    const saved: RouteIntent = {
      format: 1,
      purpose,
      input: {
        ...variables,
        environmentId: selected.environmentId,
        expectedEnvironmentVersion: selected.environmentVersion,
        workloadId: selected.workloadId,
        hostname,
        reviewSha256: review.reviewSha256,
        requestId: crypto.randomUUID(),
      },
    };
    try {
      sessionStorage.setItem(recoveryKey, JSON.stringify(saved));
    } catch {
      setStorageBlocked(true);
      setError(d("readFailed"));
      return;
    }
    setIntent(saved);
    setOperation(null);
    working.current = true;
    setBusy(true);
    invalidate();
    const captured = operationEpoch.current;
    try {
      const result = await client.mutate<
        PrepareDomainAppRouteMutation & ConnectDomainAppRouteMutation
      >({
        mutation: purpose === "prepare" ? PREPARE_DOMAIN_APP_ROUTE : CONNECT_DOMAIN_APP_ROUTE,
        variables: { input: saved.input },
      });
      if (!live.current || captured !== operationEpoch.current) return;
      const envelope =
        purpose === "prepare"
          ? result.data?.prepareManagedDomainAppRoute
          : result.data?.connectManagedDomainAppRoute;
      if (
        envelope?.ok === true &&
        Array.isArray(envelope.errors) &&
        envelope.errors.length === 0 &&
        envelope.data &&
        correlatedRouteOperation(envelope.data, saved)
      )
        setOperation(envelope.data);
      else setError(d("readFailed"));
    } catch {
      if (live.current && captured === operationEpoch.current) setError(d("readFailed"));
    } finally {
      if (live.current) {
        working.current = false;
        setBusy(false);
      }
    }
  }
  async function reconcile() {
    if (!intent || !operation || working.current || !operation.canReconcile || !admitted) return;
    // A fresh exact read must establish current action flags/version before the write.
    working.current = true;
    setBusy(true);
    invalidate();
    const captured = operationEpoch.current;
    try {
      const fresh = await client.query<DomainAppRouteOperationQuery>({
        query: DOMAIN_APP_ROUTE_OPERATION,
        variables: { input: { domainId: context.domainId, requestId: intent.input.requestId } },
        fetchPolicy: "no-cache",
        context: { queryDeduplication: false },
      });
      const current = fresh.data?.managedDomainAppRouteOperation;
      if (!live.current || captured !== operationEpoch.current) return;
      if (!current || !correlatedRouteOperation(current, intent) || !current.canReconcile)
        throw new Error();
      const input =
        intent.reconcile?.operationId === current.id &&
        intent.reconcile.expectedVersion === current.version
          ? intent.reconcile
          : {
              operationId: current.id,
              expectedVersion: current.version,
              requestId: crypto.randomUUID(),
            };
      const saved = { ...intent, reconcile: input };
      sessionStorage.setItem(recoveryKey, JSON.stringify(saved));
      setIntent(saved);
      const result = await client.mutate<ReconcileDomainAppRouteMutation>({
        mutation: RECONCILE_DOMAIN_APP_ROUTE,
        variables: { input },
      });
      if (!live.current || captured !== operationEpoch.current) return;
      const envelope = result.data?.reconcileManagedDomainAppRoute;
      if (
        envelope?.ok !== true ||
        !Array.isArray(envelope.errors) ||
        envelope.errors.length ||
        !envelope.data ||
        !correlatedRouteOperation(envelope.data, saved) ||
        envelope.data.id !== current.id ||
        envelope.data.version < current.version
      )
        throw new Error();
      setOperation(envelope.data);
    } catch {
      if (live.current && captured === operationEpoch.current) {
        setOperation(null);
        setError(d("readFailed"));
      }
    } finally {
      if (live.current) {
        working.current = false;
        setBusy(false);
      }
    }
  }
  async function continueAfterPrepare() {
    if (
      !intent ||
      intent.purpose !== "prepare" ||
      operation?.status !== "ready" ||
      working.current ||
      !admitted
    )
      return;
    working.current = true;
    setBusy(true);
    invalidate();
    const captured = operationEpoch.current;
    try {
      const result = await client.query<DomainAppRouteOperationQuery>({
        query: DOMAIN_APP_ROUTE_OPERATION,
        variables: { input: { domainId: context.domainId, requestId: intent.input.requestId } },
        fetchPolicy: "no-cache",
        context: { queryDeduplication: false },
      });
      const row = result.data?.managedDomainAppRouteOperation;
      if (
        !row ||
        !correlatedRouteOperation(row, intent) ||
        row.status !== "ready" ||
        !(await context.onReload())
      )
        throw new Error();
      if (!live.current) return;
      // This known completed preparation is followed by a distinct, freshly reviewed Connect operation.
      sessionStorage.removeItem(recoveryKey);
      setIntent(null);
      setOperation(null);
      setTarget(null);
      setCursors([null]);
      setReview(null);
      setError(null);
      await q.refetch();
    } catch {
      if (live.current && captured === operationEpoch.current) setError(d("readFailed"));
    } finally {
      if (live.current) {
        working.current = false;
        setBusy(false);
      }
    }
  }
  const targetViews = targets.map((t) => ({
    kind: t.kind as "DEPLOYMENT" | "FAAS_HTTP_API",
    key: routeTargetKey(t),
    app: t.appName,
    environment: t.environmentName,
    workload: t.workloadName,
    cluster: t.clusterName,
  }));
  const shownTarget = selected && targetViews.find((t) => t.key === routeTargetKey(selected));
  const view: RouteReviewView | null =
    review && shownTarget
      ? {
          hostname: review.hostname,
          target: shownTarget,
          records:
            review.allocationState === "OBSERVED"
              ? review.records.map((r) => `${r.name} ${r.type} → ${r.target} (${r.targetZoneId})`)
              : [],
          recordState:
            review.allocationState === "PENDING_NATIVE_DOMAIN"
              ? "pending"
              : review.records.length
                ? "proposed"
                : "unavailable",
          certificate: review.certificateStatus,
          api:
            review.kind === "FAAS_HTTP_API" && review.apiId && review.stage && review.functionArn
              ? { id: review.apiId, stage: review.stage, functionReference: review.functionArn }
              : null,
          mode: review.writer === "MANUAL" ? "manual" : "managed",
          endpoint: review.apiEndpoint ?? review.records[0]?.target ?? d("unknown"),
          tls: d("unknown"),
          checkedAt: d("unknown"),
          reason: review.reason,
        }
      : null;
  return {
    open,
    blockedReason: storageBlocked ? t("storageUnavailable") : null,
    onOpenChange: setOpen,
    zone: context.zone,
    hostnameKind: kind,
    subdomain,
    onHostname: (k, s) => {
      if (locked || working.current) return;
      invalidate();
      setKind(k);
      setSubdomain(s);
    },
    hostnameValid: !!hostname,
    targets: targetViews,
    targetKey,
    onTarget: (key) => {
      if (locked || working.current) return;
      invalidate();
      setTarget(key);
    },
    readState:
      !admitted || storageBlocked
        ? "refused"
        : q.loading || p.loading || !loaded
          ? "loading"
          : q.error || p.error || error
            ? "error"
            : !allowed
              ? "refused"
              : pageValid
                ? "ready"
                : "error",
    readReason: storageBlocked
      ? "RECOVERY_STORAGE_UNAVAILABLE"
      : (support?.reason ?? error ?? null),
    onRefresh: refresh,
    hasPreviousPage: cursors.length > 1,
    hasNextPage: pageValid && page.hasNextPage,
    onPreviousPage: () => {
      invalidate();
      setTarget(null);
      setCursors((c) => c.slice(0, -1));
    },
    onNextPage: () => {
      if (pageValid && page.hasNextPage && page.nextCursor) {
        invalidate();
        setTarget(null);
        setCursors((c) => [...c, page.nextCursor!]);
      }
    },
    review: view,
    reviewing,
    reviewError: error,
    canReview: !locked && admitted && allowed && pageValid && !!selected && !!hostname,
    onReview,
    canPrepare: reviewed && !locked && review!.writer === "ROUTE53_ALIAS" && review!.canPrepare,
    canAttach: reviewed && !locked && review!.writer === "ROUTE53_ALIAS" && review!.canAttach,
    onPrepare: () => void send("prepare"),
    onAttach: () => void send("connect"),
    busy,
    outcome: intent
      ? operation
        ? operation.status === "unknown"
          ? "unknown"
          : "recorded"
        : "unknown"
      : "none",
    onRecover: () => void recover(),
    operation: operation
      ? {
          id: operation.id,
          status: operation.status,
          purpose: operation.purpose,
          dns: operation.dnsState,
          endpoint: operation.ingressState,
          tls: operation.tlsState,
          reason: operation.reason,
        }
      : null,
    onReconcile: operation?.canReconcile && admitted ? () => void reconcile() : undefined,
    onContinueAfterPrepare:
      intent?.purpose === "prepare" && operation?.status === "ready" && admitted
        ? () => void continueAfterPrepare()
        : undefined,
  };
}
