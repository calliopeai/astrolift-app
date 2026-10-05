// Generated from the mounted routing draft; only transitively required declarations retained.
export type Maybe<T> = T | null;

export type InputMaybe<T> = Maybe<T>;

export type Exact<T extends { [key: string]: unknown }> = { [K in keyof T]: T[K] };

/** All built-in and custom scalars, mapped to their actual values */
export type Scalars = {
  ID: { input: string; output: string };
  String: { input: string; output: string };
  Boolean: { input: boolean; output: boolean };
  Int: { input: number; output: number };
  Float: { input: number; output: number };
  /** Date (isoformat) */
  Date: { input: any; output: any };
  /** Date with time (isoformat) */
  DateTime: { input: string; output: string };
  /** UUID v7 — the platform's external identifier. */
  GUID: { input: string; output: string };
  /** The `JSON` scalar type represents JSON values as specified by [ECMA-404](https://ecma-international.org/wp-content/uploads/ECMA-404_2nd_edition_december_2017.pdf). */
  JSON: { input: unknown; output: unknown };
  UUID: { input: any; output: any };
  /** Represents NULL values */
  Void: { input: any; output: any };
};

export type ManagedDomainAppRouteOperationInput = {
  domainId: Scalars["GUID"]["input"];
  requestId: Scalars["GUID"]["input"];
};

export type ManagedDomainAppRouteReconcileInput = {
  expectedVersion: Scalars["Int"]["input"];
  operationId: Scalars["GUID"]["input"];
  requestId: Scalars["GUID"]["input"];
};

export type ManagedDomainAppRouteTargetInput = {
  domainId: Scalars["GUID"]["input"];
  environmentId: Scalars["GUID"]["input"];
  expectedDomainVersion: Scalars["Int"]["input"];
  expectedEnvironmentVersion: Scalars["Int"]["input"];
  hostname: Scalars["String"]["input"];
  workloadId: Scalars["GUID"]["input"];
};

export type ManagedDomainAppRouteWriteInput = {
  domainId: Scalars["GUID"]["input"];
  environmentId: Scalars["GUID"]["input"];
  expectedDomainVersion: Scalars["Int"]["input"];
  expectedEnvironmentVersion: Scalars["Int"]["input"];
  hostname: Scalars["String"]["input"];
  requestId: Scalars["GUID"]["input"];
  reviewSha256: Scalars["String"]["input"];
  workloadId: Scalars["GUID"]["input"];
};

export type DomainAppRouteSupportQueryVariables = Exact<{
  domainId: Scalars["GUID"]["input"];
  expectedDomainVersion: Scalars["Int"]["input"];
}>;

export type DomainAppRouteSupportQuery = {
  managedDomainAppRouteSupport: {
    domainId: string;
    version: number;
    allowed: boolean;
    reason: string;
    writer: string;
  };
};

export type DomainAppRouteTargetsQueryVariables = Exact<{
  domainId: Scalars["GUID"]["input"];
  expectedDomainVersion: Scalars["Int"]["input"];
  after?: InputMaybe<Scalars["String"]["input"]>;
}>;

export type DomainAppRouteTargetsQuery = {
  managedDomainAppTargetsPage: {
    nextCursor?: string | null;
    hasNextPage: boolean;
    items: Array<{
      kind: string;
      appId: string;
      appName: string;
      appVersion: number;
      environmentId: string;
      environmentName: string;
      environmentVersion: number;
      workloadId: string;
      workloadName: string;
      workloadVersion: number;
      clusterId: string;
      clusterName: string;
      clusterVersion: number;
    }>;
  };
};

export type DomainAppRouteReviewQueryVariables = Exact<{
  input: ManagedDomainAppRouteTargetInput;
}>;

export type DomainAppRouteReviewQuery = {
  managedDomainAppRouteReview: {
    kind: string;
    domainId: string;
    domainVersion: number;
    environmentId: string;
    environmentVersion: number;
    workloadId: string;
    workloadVersion: number;
    clusterId: string;
    clusterVersion: number;
    hostname: string;
    reviewSha256: string;
    writer: string;
    certificateStatus: string;
    allocationState: string;
    apiId?: string | null;
    apiEndpoint?: string | null;
    stage?: string | null;
    functionArn?: string | null;
    sourceFingerprint?: string | null;
    canPrepare: boolean;
    canAttach: boolean;
    reason: string;
    records: Array<{
      name: string;
      type: string;
      target?: string | null;
      targetZoneId?: string | null;
    }>;
  };
};

export type DomainAppRouteOperationFieldsFragment = {
  id: string;
  version: number;
  requestId: string;
  domainId: string;
  environmentId: string;
  workloadId: string;
  customDomainId?: string | null;
  hostname: string;
  purpose: string;
  status: string;
  reason: string;
  dnsState: string;
  ingressState: string;
  tlsState: string;
  canReconcile: boolean;
};

export type DomainAppRouteOperationQueryVariables = Exact<{
  input: ManagedDomainAppRouteOperationInput;
}>;

export type DomainAppRouteOperationQuery = {
  managedDomainAppRouteOperation?: {
    id: string;
    version: number;
    requestId: string;
    domainId: string;
    environmentId: string;
    workloadId: string;
    customDomainId?: string | null;
    hostname: string;
    purpose: string;
    status: string;
    reason: string;
    dnsState: string;
    ingressState: string;
    tlsState: string;
    canReconcile: boolean;
  } | null;
};

export type PrepareDomainAppRouteMutationVariables = Exact<{
  input: ManagedDomainAppRouteWriteInput;
}>;

export type PrepareDomainAppRouteMutation = {
  prepareManagedDomainAppRoute: {
    ok: boolean;
    errors: Array<{ code: string; message: string; field?: string | null }>;
    data?: {
      id: string;
      version: number;
      requestId: string;
      domainId: string;
      environmentId: string;
      workloadId: string;
      customDomainId?: string | null;
      hostname: string;
      purpose: string;
      status: string;
      reason: string;
      dnsState: string;
      ingressState: string;
      tlsState: string;
      canReconcile: boolean;
    } | null;
  };
};

export type ConnectDomainAppRouteMutationVariables = Exact<{
  input: ManagedDomainAppRouteWriteInput;
}>;

export type ConnectDomainAppRouteMutation = {
  connectManagedDomainAppRoute: {
    ok: boolean;
    errors: Array<{ code: string; message: string; field?: string | null }>;
    data?: {
      id: string;
      version: number;
      requestId: string;
      domainId: string;
      environmentId: string;
      workloadId: string;
      customDomainId?: string | null;
      hostname: string;
      purpose: string;
      status: string;
      reason: string;
      dnsState: string;
      ingressState: string;
      tlsState: string;
      canReconcile: boolean;
    } | null;
  };
};

export type ReconcileDomainAppRouteMutationVariables = Exact<{
  input: ManagedDomainAppRouteReconcileInput;
}>;

export type ReconcileDomainAppRouteMutation = {
  reconcileManagedDomainAppRoute: {
    ok: boolean;
    errors: Array<{ code: string; message: string; field?: string | null }>;
    data?: {
      id: string;
      version: number;
      requestId: string;
      domainId: string;
      environmentId: string;
      workloadId: string;
      customDomainId?: string | null;
      hostname: string;
      purpose: string;
      status: string;
      reason: string;
      dnsState: string;
      ingressState: string;
      tlsState: string;
      canReconcile: boolean;
    } | null;
  };
};
