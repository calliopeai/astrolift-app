import type {
  DomainAppRouteOperationFieldsFragment as Operation,
  DomainAppRouteReviewQuery,
  DomainAppRouteTargetsQuery,
  ManagedDomainAppRouteWriteInput,
} from "@/graphql/__generated__/domain-app-route";
export type RouteTarget =
  DomainAppRouteTargetsQuery["managedDomainAppTargetsPage"]["items"][number];
export type RouteReview = DomainAppRouteReviewQuery["managedDomainAppRouteReview"];
export type RouteIntent = {
  format: 1;
  purpose: "prepare" | "connect";
  input: ManagedDomainAppRouteWriteInput;
  reconcile?: { operationId: string; expectedVersion: number; requestId: string };
};
export const routeGuid = (value: unknown): value is string =>
  typeof value === "string" && /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(value);
export const routeVersion = (value: unknown): value is number =>
  Number.isSafeInteger(value) && Number(value) > 0;
export const routeSha = (value: unknown): value is string =>
  typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
export function routeHostname(
  zone: string,
  kind: "apex" | "subdomain",
  label: string
): string | null {
  const hostname = kind === "apex" ? zone : `${label}.${zone}`;
  return hostname.length <= 253 &&
    hostname === hostname.toLowerCase() &&
    hostname
      .split(".")
      .every((s) => s.length <= 63 && /^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$/.test(s)) &&
    (kind === "apex" || label.length > 0)
    ? hostname
    : null;
}
export function validRouteTarget(row: RouteTarget): boolean {
  return (
    !!row &&
    ["DEPLOYMENT", "FAAS_HTTP_API"].includes(row.kind) &&
    [row.appId, row.environmentId, row.workloadId, row.clusterId].every(routeGuid) &&
    [row.appVersion, row.environmentVersion, row.workloadVersion, row.clusterVersion].every(
      routeVersion
    ) &&
    [row.appName, row.environmentName, row.workloadName, row.clusterName].every(
      (v) => typeof v === "string" && v.length > 0
    )
  );
}
export const routeTargetKey = (row: RouteTarget) => `${row.environmentId}:${row.workloadId}`;
export function validRouteReview(
  row: RouteReview,
  target: RouteTarget,
  domainId: string,
  version: number,
  hostname: string
): boolean {
  return (
    !!row &&
    row.domainId === domainId &&
    row.domainVersion === version &&
    row.environmentId === target.environmentId &&
    row.environmentVersion === target.environmentVersion &&
    row.workloadId === target.workloadId &&
    row.workloadVersion === target.workloadVersion &&
    row.clusterId === target.clusterId &&
    row.clusterVersion === target.clusterVersion &&
    row.kind === target.kind &&
    row.hostname === hostname &&
    routeSha(row.reviewSha256) &&
    ["ROUTE53_ALIAS", "MANUAL", "UNSUPPORTED"].includes(row.writer) &&
    ["OBSERVED", "PENDING_NATIVE_DOMAIN"].includes(row.allocationState) &&
    typeof row.canPrepare === "boolean" &&
    typeof row.canAttach === "boolean" &&
    Array.isArray(row.records) &&
    row.records.every(
      (r) =>
        r.name === hostname &&
        r.type === "A_ALIAS" &&
        (row.allocationState === "PENDING_NATIVE_DOMAIN"
          ? r.target === null && r.targetZoneId === null
          : typeof r.target === "string" &&
            r.target.length > 0 &&
            typeof r.targetZoneId === "string" &&
            r.targetZoneId.length > 0)
    ) &&
    (row.allocationState !== "PENDING_NATIVE_DOMAIN" || row.kind === "FAAS_HTTP_API") &&
    (row.kind !== "FAAS_HTTP_API" ||
      !!(
        row.apiId?.trim() &&
        row.apiEndpoint?.trim() &&
        row.stage === "$default" &&
        row.functionArn?.trim() &&
        routeSha(row.sourceFingerprint)
      ))
  );
}
export function validRouteIntent(value: RouteIntent, domainId: string): boolean {
  const i = value?.input;
  return (
    value?.format === 1 &&
    ["prepare", "connect"].includes(value.purpose) &&
    !!i &&
    i.domainId === domainId &&
    [i.domainId, i.environmentId, i.workloadId, i.requestId].every(routeGuid) &&
    [i.expectedDomainVersion, i.expectedEnvironmentVersion].every(routeVersion) &&
    routeSha(i.reviewSha256) &&
    typeof i.hostname === "string" &&
    routeHostname(i.hostname, "apex", "") === i.hostname &&
    (!value.reconcile ||
      (routeGuid(value.reconcile.operationId) &&
        routeGuid(value.reconcile.requestId) &&
        routeVersion(value.reconcile.expectedVersion)))
  );
}
export function correlatedRouteOperation(row: Operation, intent: RouteIntent): boolean {
  return (
    !!row &&
    routeGuid(row.id) &&
    routeVersion(row.version) &&
    row.requestId === intent.input.requestId &&
    row.domainId === intent.input.domainId &&
    row.environmentId === intent.input.environmentId &&
    row.workloadId === intent.input.workloadId &&
    row.hostname === intent.input.hostname &&
    row.purpose === intent.purpose &&
    ["accepted", "configuring", "pending", "ready", "unknown", "refused"].includes(row.status) &&
    (row.customDomainId == null || routeGuid(row.customDomainId)) &&
    typeof row.canReconcile === "boolean" &&
    [row.reason, row.dnsState, row.ingressState, row.tlsState].every(
      (v) => typeof v === "string" && v.length <= 256
    )
  );
}
