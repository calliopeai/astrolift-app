import { gql } from "@apollo/client";

export const GET_MANAGED_DOMAIN = gql`
  query ManagedDomain($domainId: GUID!) {
    astroliftManagedDomain(domainId: $domainId) {
      id
      version
      zone
      dnsDriver
      defaultFor
      isWildcardManaged
      organizationSlug
      createdAt
      provisionState
      provisionNameservers
      provisionValidationRecords
      provisionClusterId
      verificationState
      verifiedAt
      challengeRecordName
      challengeRecordValue
      delegationCheck
      delegationConnectionId
      delegationZoneName
      delegationWrite
    }
  }
`;

export const MANAGED_DOMAIN_ACTIONS = gql`
  query ManagedDomainActions {
    astroliftManagedDomainActions {
      canCreate
      canDelete
      canRevalidate
    }
  }
`;

export const MANAGED_DOMAIN_DIAGNOSTICS = gql`
  query ManagedDomainDiagnostics($domainId: GUID!, $expectedVersion: Int!) {
    astroliftManagedDomainDiagnostics(domainId: $domainId, expectedVersion: $expectedVersion) {
      id
      version
      zone
      verificationState
      provisionState
      provisionClusterId
      checkedAt
      checks {
        key
        state
        perspective
        checkedAt
        reason
        expected
        observed
      }
      providerZone {
        bindingSource
        state
        reason
        checkedAt
        zoneId
        zoneName
        privateZone
        nameservers
        truncated
        records {
          name
          type
          ttl
          values
          aliasTarget
          aliasZoneId
          evaluateTargetHealth
          proxied
          priority
          routing {
            policy
            reason
            setIdentifier
            weight
            region
            failover
            geoLocation {
              continentCode
              countryCode
              subdivisionCode
            }
            geoProximity {
              awsRegion
              localZoneGroup
              coordinates {
                latitude
                longitude
              }
              bias
            }
            cidr {
              collectionId
              locationName
            }
            multiValueAnswer
            healthCheckId
            trafficPolicyInstanceId
          }
        }
      }
      routes {
        appId
        appName
        appSlug
        environmentId
        environmentName
        recordedUrl
        hostname
        clusterId
        clusterName
        clusterSlug
        ingressClass
        observedState
      }
      routesTruncated
      certificate {
        state
        reason
        checkedAt
        status
        validationRecords
      }
      actions {
        canCreate
        canDelete
        canRevalidate
        canUpdate
        canSetDefaults
        defaultUpdateReason
        defaultsFingerprint
      }
    }
  }
`;

export const UPDATE_MANAGED_DOMAIN_DEFAULTS = gql`
  mutation UpdateManagedDomainDefaults($input: UpdateManagedDomainInput!) {
    updateManagedDomain(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        id
        version
        zone
        defaultFor
        organizationSlug
      }
    }
  }
`;

export const MANAGED_DOMAIN_PROBE = gql`
  query ManagedDomainProbe(
    $domainId: GUID!
    $expectedVersion: Int!
    $hostname: String!
    $tool: ManagedDomainProbeTool!
    $recordType: ManagedDomainRecordType!
  ) {
    astroliftManagedDomainProbe(
      domainId: $domainId
      expectedVersion: $expectedVersion
      hostname: $hostname
      tool: $tool
      recordType: $recordType
    ) {
      state
      perspective
      checkedAt
      reason
      hostname
      tool
      recordType
      values
      publicAddress
      httpStatus
      tlsVerified
      latencyMs
    }
  }
`;

export const DNS_CONNECTION_SUPPORT = gql`
  query DnsConnectionSupport {
    dnsProviderConnectionSupport {
      allowed
      reason
      apiTokenSupported
      oauthConfigured
      oauthSetupReason
      dnsWritesSupported
    }
  }
`;
export const DNS_CONNECTIONS_PAGE = gql`
  query DnsConnectionsPage($page: Int!, $pageSize: Int!) {
    dnsProviderConnectionsPage(page: $page, pageSize: $pageSize) {
      page
      pageSize
      totalCount
      nextCursor
      items {
        id
        version
        name
        provider
        authMethod
        state
        revocationState
        verifiedAt
        expiresAt
        dnsWritesSupported
        delegationZoneName
      }
    }
  }
`;
export const CONNECT_CLOUDFLARE_TOKEN = gql`
  mutation ConnectCloudflareToken($input: ConnectCloudflareDnsTokenInput!) {
    connectCloudflareDnsToken(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        id
        version
        name
        provider
        authMethod
        state
        revocationState
        verifiedAt
        expiresAt
        dnsWritesSupported
      }
    }
  }
`;
export const BEGIN_CLOUDFLARE_OAUTH = gql`
  mutation BeginCloudflareOAuth($input: BeginCloudflareDnsOAuthInput!) {
    beginCloudflareDnsOAuth(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        authorizationUrl
      }
    }
  }
`;
export const RETEST_DNS_CONNECTION = gql`
  mutation RetestDnsConnection($input: CloudflareDnsConnectionInput!) {
    retestDnsProviderConnection(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        id
        version
        name
        provider
        authMethod
        state
        revocationState
        verifiedAt
        expiresAt
        dnsWritesSupported
      }
    }
  }
`;
export const DISCONNECT_DNS_CONNECTION = gql`
  mutation DisconnectDnsConnection($input: CloudflareDnsConnectionInput!) {
    disconnectDnsProviderConnection(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        id
        version
        name
        provider
        authMethod
        state
        revocationState
        verifiedAt
        expiresAt
        dnsWritesSupported
      }
    }
  }
`;
export const CLOUDFLARE_ZONES = gql`
  query CloudflareZones($input: CloudflareDnsConnectionInput!) {
    cloudflareDnsZones(input: $input) {
      complete
      reason
      items {
        id
        name
        accountId
        status
        nameServers
      }
    }
  }
`;
export const CLOUDFLARE_RECORDS = gql`
  query CloudflareRecords($input: CloudflareDnsRecordsInput!) {
    cloudflareDnsRecords(input: $input) {
      complete
      reason
      items {
        id
        name
        type
        content
        ttl
        proxied
        priority
      }
    }
  }
`;
export const REGISTER_CLOUDFLARE_ZONE = gql`
  mutation RegisterCloudflareZone($input: RegisterCloudflareDnsZoneInput!) {
    registerCloudflareDnsZone(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        domainId
        domainVersion
        connectionId
        connectionVersion
        dnsWritesSupported
        zone {
          id
          name
          accountId
          status
          nameServers
        }
        verificationState
        verificationRecordName
        verificationRecordValue
      }
    }
  }
`;
export const ATTACH_CLOUDFLARE_ZONE = gql`
  mutation AttachCloudflareZone($input: AttachCloudflareDnsZoneInput!) {
    attachCloudflareDnsZone(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        domainId
        domainVersion
        connectionId
        connectionVersion
        dnsWritesSupported
        zone {
          id
          name
          accountId
          status
          nameServers
        }
        verificationState
        verificationRecordName
        verificationRecordValue
      }
    }
  }
`;
export const ROUTE53_DNS_CLUSTERS = gql`
  query Route53DnsClusters {
    route53DnsClusters {
      complete
      reason
      items {
        id
        name
        region
        shared
      }
    }
  }
`;
export const ROUTE53_DNS_ZONES = gql`
  query Route53DnsZones($input: Route53DnsZonesInput!) {
    route53DnsZones(input: $input) {
      complete
      reason
      truncated
      items {
        id
        name
        privateZone
        recordCount
      }
    }
  }
`;
export const ATTACH_ROUTE53_ZONE = gql`
  mutation AttachRoute53Zone($input: AttachRoute53DnsZoneInput!) {
    attachRoute53DnsZone(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        domainId
        domainVersion
        clusterId
        accountId
        zone {
          id
          name
          privateZone
          recordCount
        }
        verificationState
      }
    }
  }
`;
export const VERIFY_DNS_DOMAIN = gql`
  mutation VerifyDnsDomain($input: VerifyManagedDomainInput!) {
    verifyManagedDomain(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        zone
        verified
        message
      }
    }
  }
`;

export const DNS_DOMAIN_BINDING = gql`
  query DnsDomainBinding($domainId: GUID!) {
    dnsProviderDomainBinding(domainId: $domainId) {
      domainId
      domainVersion
      state
      connectionId
      connectionVersion
      currentConnectionVersion
      zoneId
      zoneName
      dnsWritesSupported
      canVerify
    }
  }
`;
// #1787: a separate Cloudflare token that may write delegating NS records into
// one parent zone, and the binding that names it for a managed domain. Both
// need a fresh sign-in; the token is never read back.
export const GRANT_CLOUDFLARE_DELEGATION = gql`
  mutation GrantCloudflareDelegation($input: GrantCloudflareDelegationInput!) {
    grantCloudflareDelegation(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        id
        version
        dnsWritesSupported
        delegationZoneName
      }
    }
  }
`;
export const BIND_MANAGED_DOMAIN_DELEGATION = gql`
  mutation BindManagedDomainDelegation($input: BindManagedDomainDelegationInput!) {
    bindManagedDomainDelegation(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        id
        version
        delegationZoneName
      }
    }
  }
`;
