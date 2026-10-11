import { gql } from "@apollo/client";

export const DOMAIN_APP_ROUTE_SUPPORT = gql`
  query DomainAppRouteSupport($domainId: GUID!, $expectedDomainVersion: Int!) {
    managedDomainAppRouteSupport(
      domainId: $domainId
      expectedDomainVersion: $expectedDomainVersion
    ) {
      domainId
      version
      allowed
      reason
      writer
    }
  }
`;
export const DOMAIN_APP_ROUTE_TARGETS = gql`
  query DomainAppRouteTargets($domainId: GUID!, $expectedDomainVersion: Int!, $after: String) {
    managedDomainAppTargetsPage(
      domainId: $domainId
      expectedDomainVersion: $expectedDomainVersion
      first: 20
      after: $after
    ) {
      items {
        kind
        appId
        appName
        appVersion
        environmentId
        environmentName
        environmentVersion
        workloadId
        workloadName
        workloadVersion
        clusterId
        clusterName
        clusterVersion
      }
      nextCursor
      hasNextPage
    }
  }
`;
export const DOMAIN_APP_ROUTE_REVIEW = gql`
  query DomainAppRouteReview($input: ManagedDomainAppRouteTargetInput!) {
    managedDomainAppRouteReview(input: $input) {
      kind
      domainId
      domainVersion
      environmentId
      environmentVersion
      workloadId
      workloadVersion
      clusterId
      clusterVersion
      hostname
      reviewSha256
      writer
      records {
        name
        type
        target
        targetZoneId
      }
      certificateStatus
      allocationState
      apiId
      apiEndpoint
      stage
      functionArn
      sourceFingerprint
      canPrepare
      canAttach
      reason
    }
  }
`;
export const DOMAIN_APP_ROUTE_OPERATION_FIELDS = gql`
  fragment DomainAppRouteOperationFields on ManagedDomainAppRouteOperation {
    id
    version
    requestId
    domainId
    environmentId
    workloadId
    customDomainId
    hostname
    purpose
    status
    reason
    dnsState
    ingressState
    tlsState
    canReconcile
  }
`;
export const DOMAIN_APP_ROUTE_OPERATION = gql`
  query DomainAppRouteOperation($input: ManagedDomainAppRouteOperationInput!) {
    managedDomainAppRouteOperation(input: $input) {
      ...DomainAppRouteOperationFields
    }
  }
  ${DOMAIN_APP_ROUTE_OPERATION_FIELDS}
`;
export const PREPARE_DOMAIN_APP_ROUTE = gql`
  mutation PrepareDomainAppRoute($input: ManagedDomainAppRouteWriteInput!) {
    prepareManagedDomainAppRoute(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        ...DomainAppRouteOperationFields
      }
    }
  }
  ${DOMAIN_APP_ROUTE_OPERATION_FIELDS}
`;
export const CONNECT_DOMAIN_APP_ROUTE = gql`
  mutation ConnectDomainAppRoute($input: ManagedDomainAppRouteWriteInput!) {
    connectManagedDomainAppRoute(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        ...DomainAppRouteOperationFields
      }
    }
  }
  ${DOMAIN_APP_ROUTE_OPERATION_FIELDS}
`;
export const RECONCILE_DOMAIN_APP_ROUTE = gql`
  mutation ReconcileDomainAppRoute($input: ManagedDomainAppRouteReconcileInput!) {
    reconcileManagedDomainAppRoute(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        ...DomainAppRouteOperationFields
      }
    }
  }
  ${DOMAIN_APP_ROUTE_OPERATION_FIELDS}
`;
