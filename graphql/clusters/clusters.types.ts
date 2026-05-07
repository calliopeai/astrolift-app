export type AstroliftGuid = string;

export interface AstroliftTenantCluster {
  id: AstroliftGuid;
  slug: string;
  name: string;
  organizationSlug: string | null;
  providerPluginSlug: string;
  region: string;
  endpoint: string;
  authMethod: string;
  ingressClass: string;
  isActive: boolean;
  capabilities: Record<string, unknown>;
  capabilitiesProbedAt: string | null;
  createdAt: string;
}

export interface AstroliftManagedDomain {
  id: AstroliftGuid;
  zone: string;
  organizationSlug: string | null;
  dnsDriver: string;
  defaultFor: "tenant_apps" | "preview_envs" | "both" | "none";
  isWildcardManaged: boolean;
  createdAt: string;
}

export interface AstroliftProviderPlugin {
  id: AstroliftGuid;
  slug: string;
  name: string;
  version: string;
  capabilitiesManifest: Record<string, unknown>;
  isEnabled: boolean;
}
