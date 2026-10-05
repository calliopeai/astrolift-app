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
      actions {
        canCreate
        canDelete
        canRevalidate
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
