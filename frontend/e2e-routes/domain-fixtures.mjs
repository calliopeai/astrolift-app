// Metadata-only controlled HTTP responses for the actual production Next DNS journey.
// No provider transport or real credential/record write occurs in this fixture.
const domainId = "55555555-5555-4555-8555-555555555555";
const connectionId = "66666666-6666-4666-8666-666666666666";
const timestamp = "2026-10-04T01:00:00Z";
const zone = {
  id: "0123456789abcdef0123456789abcdef",
  name: "cloudflare.example",
  accountId: "fedcba9876543210fedcba9876543210",
  status: "active",
  nameServers: ["ada.ns.cloudflare.com", "ben.ns.cloudflare.com"],
};
const connection = {
  id: connectionId,
  version: 3,
  name: "Controlled read-only DNS",
  provider: "CLOUDFLARE",
  authMethod: "API_TOKEN",
  state: "ACTIVE",
  revocationState: "NOT_REQUESTED",
  verifiedAt: timestamp,
  expiresAt: null,
  dnsWritesSupported: false,
};
const initial = {
  id: domainId,
  version: 7,
  zone: "acme.example",
  dnsDriver: "route53",
  defaultFor: "tenant_apps",
  isWildcardManaged: false,
  challengeRecordKind: "TXT",
  challengeRecordName: "_astrolift-verify.acme.example",
  challengeRecordValue: "astrolift-verify=controlled-proof",
  verificationState: "pending",
  verifiedAt: null,
  provisionState: "mark_active",
  provisionNameservers: ["ns-0.awsdns-00.com", "ns-1.awsdns-01.net"],
  provisionValidationRecords: [],
  provisionError: null,
  provisionClusterId: "33333333-3333-4333-8333-333333333333",
  organizationSlug: "fixture",
  createdAt: timestamp,
};
let domain = { ...initial };
let connected = false;
export const domainWrites = [];
export function resetDomainFixtures() {
  domain = { ...initial };
  connected = false;
  domainWrites.length = 0;
}
export function domainFixture(field, args, role, parent) {
  const owner = role === "owner";
  if (parent === "Mutation") {
    if (
      ![
        "registerCloudflareDnsZone",
        "attachCloudflareDnsZone",
        "verifyManagedDomain",
        "connectCloudflareDnsToken",
        "retestDnsProviderConnection",
        "disconnectDnsProviderConnection",
      ].includes(field)
    )
      return undefined;
    if (!owner) throw new Error("Controlled DNS authority refused");
    const input = args.input;
    if (field === "verifyManagedDomain") {
      if (input.zone !== domain.zone) throw new Error("Controlled domain target differs");
      domainWrites.push({ field, input });
      domain.verificationState = "verified";
      return {
        ok: true,
        errors: [],
        data: { zone: domain.zone, verified: true, message: "Ownership observed" },
      };
    }
    if (field === "connectCloudflareDnsToken") {
      domainWrites.push({
        field,
        input: {
          organizationId: input.organizationId,
          name: input.name,
          tokenProvided: Boolean(input.token),
        },
      });
      return { ok: true, errors: [], data: connection };
    }
    if (field === "registerCloudflareDnsZone" || field === "attachCloudflareDnsZone") {
      if (
        input.connectionId !== connectionId ||
        input.expectedConnectionVersion !== 3 ||
        input.zoneId !== zone.id ||
        input.zoneName !== zone.name ||
        (field === "attachCloudflareDnsZone" &&
          (input.domainId !== domainId || input.expectedDomainVersion !== domain.version))
      )
        throw new Error("Controlled DNS tuple differs");
      domainWrites.push({ field, input });
      connected = true;
      domain = {
        ...domain,
        id:
          field === "registerCloudflareDnsZone"
            ? "77777777-7777-4777-8777-777777777777"
            : domain.id,
        zone: zone.name,
        challengeRecordName: `_astrolift-verify.${zone.name}`,
        dnsDriver: "cloudflare_read_only",
        version: domain.version + 1,
        defaultFor: "none",
        provisionState: "",
        provisionNameservers: [],
      };
      return {
        ok: true,
        errors: [],
        data: {
          domainId: domain.id,
          domainVersion: domain.version,
          connectionId,
          connectionVersion: 3,
          zone,
          verificationState: domain.verificationState,
          verificationRecordName: domain.challengeRecordName,
          verificationRecordValue: domain.challengeRecordValue,
          dnsWritesSupported: false,
        },
      };
    }
    return { ok: true, errors: [], data: connection };
  }
  if (parent !== "Query") return undefined;
  if (field === "astroliftManagedDomains") return role === "member" ? [] : [domain];
  if (field === "astroliftManagedDomain")
    return role === "member" || args.domainId !== domain.id ? null : domain;
  if (field === "astroliftManagedDomainActions")
    return { canCreate: owner, canDelete: false, canRevalidate: false };
  if (field === "dnsProviderConnectionSupport")
    return {
      allowed: owner,
      reason: owner ? "" : "PLATFORM_OPERATOR_REQUIRED",
      apiTokenSupported: true,
      oauthConfigured: false,
      oauthSetupReason: "OAUTH_CLIENT_NOT_CONFIGURED",
      dnsWritesSupported: false,
    };
  if (field === "dnsProviderConnectionsPage")
    return {
      page: args.page,
      pageSize: args.pageSize,
      totalCount: owner ? 1 : 0,
      nextCursor: null,
      items: owner ? [connection] : [],
    };
  if (field === "cloudflareDnsZones") return { complete: true, reason: "", items: [zone] };
  if (field === "cloudflareDnsRecords")
    return {
      complete: true,
      reason: "",
      items: [
        {
          id: "11111111111111111111111111111111",
          name: "www.acme.example",
          type: "CNAME",
          content: "origin.acme.example",
          ttl: 300,
          proxied: true,
          priority: null,
        },
      ],
    };
  if (field === "dnsProviderDomainBinding")
    return args.domainId !== domain.id || !owner
      ? null
      : {
          domainId: domain.id,
          domainVersion: domain.version,
          state: connected ? "CURRENT" : "UNBOUND",
          connectionId: connected ? connectionId : null,
          connectionVersion: connected ? 3 : null,
          currentConnectionVersion: connected ? 3 : null,
          zoneId: connected ? zone.id : null,
          zoneName: domain.zone,
          dnsWritesSupported: false,
          canVerify: domain.verificationState === "pending",
        };
  if (field === "astroliftManagedDomainDiagnostics") {
    if (args.domainId !== domain.id || args.expectedVersion !== domain.version)
      throw new Error("Controlled domain version differs");
    const expected = connected ? zone.nameServers : initial.provisionNameservers;
    return {
      id: domain.id,
      version: domain.version,
      zone: domain.zone,
      verificationState: domain.verificationState,
      provisionState: domain.provisionState,
      provisionClusterId: domain.provisionClusterId,
      checkedAt: timestamp,
      checks: [
        {
          key: "delegation",
          state: connected ? "OK" : "MISMATCH",
          perspective: "public_dns:1.1.1.1",
          checkedAt: timestamp,
          reason: "PUBLIC_DELEGATION_OBSERVED",
          expected,
          observed: zone.nameServers,
        },
        {
          key: "effective_tenant_apps_domain",
          state: "MISMATCH",
          perspective: "server_configuration",
          checkedAt: timestamp,
          reason: "RECORDED_DECLARATION_IS_NOT_CURRENT_PRIMARY",
          expected: [zone.name],
          observed: ["legacy.example"],
        },
        {
          key: "lookup",
          state: "OK",
          perspective: "public_dns:1.1.1.1",
          checkedAt: timestamp,
          reason: "DNS_ANSWER",
          expected: [],
          observed: expected,
        },
        {
          key: "internal_dns",
          state: "UNSUPPORTED",
          perspective: "cluster_internal_dns",
          checkedAt: timestamp,
          reason: "INTERNAL_DNS_PROBE_NOT_CONFIGURED",
          expected: [],
          observed: [],
        },
      ],
      providerZone: {
        bindingSource: connected ? "PROTECTED_CLOUDFLARE_CONNECTION" : "PLATFORM_WRITTEN",
        state: owner ? "OK" : "UNSUPPORTED",
        reason: owner ? "READ_ONLY_ZONE_OBSERVED" : "PLATFORM_OPERATOR_REQUIRED",
        checkedAt: timestamp,
        zoneId: !owner ? null : connected ? zone.id : "CONTROLLED_ROUTE53_ZONE",
        zoneName: owner ? domain.zone : null,
        privateZone: owner ? false : null,
        nameservers: owner ? expected : [],
        truncated: false,
        records: !owner
          ? []
          : [
              {
                name: "www.acme.example",
                type: "CNAME",
                ttl: 300,
                values: ["origin.acme.example"],
                aliasTarget: null,
                aliasZoneId: null,
                evaluateTargetHealth: null,
                proxied: connected ? true : null,
                priority: null,
              },
            ],
      },
      routes: [
        {
          appId: "11111111-1111-4111-8111-111111111111",
          appName: "Controlled app",
          appSlug: "fixture",
          environmentId: "11111111-1111-4111-8111-111111111111",
          environmentName: "production",
          recordedUrl: "https://www.acme.example",
          hostname: "www.acme.example",
          clusterId: domain.provisionClusterId,
          clusterName: "Controlled cluster",
          clusterSlug: "shared-fixture",
          ingressClass: "alb",
          observedState: "UNKNOWN",
        },
      ],
      routesTruncated: false,
      actions: { canCreate: owner, canDelete: owner, canRevalidate: owner && !connected },
    };
  }
  if (field === "astroliftManagedDomainProbe")
    return {
      state:
        role === "reader" && args.tool === "PING"
          ? "UNKNOWN"
          : ["PING", "TRACEROUTE"].includes(args.tool)
            ? "UNSUPPORTED"
            : "OK",
      perspective: "public_dns:1.1.1.1",
      checkedAt: timestamp,
      reason:
        role === "reader"
          ? args.tool === "PING"
            ? "ICMP_TIMEOUT"
            : "DNS_NO_DATA"
          : ["LOOKUP", "DIG"].includes(args.tool)
            ? "DNS_ANSWER"
            : "CONTROLLED_PUBLIC_CHECK",
      hostname: args.hostname,
      tool: args.tool,
      recordType: args.recordType,
      values: role === "reader" ? [] : ["controlled-dns-answer"],
      publicAddress: null,
      httpStatus: null,
      tlsVerified: null,
      latencyMs: null,
    };
  return undefined;
}
