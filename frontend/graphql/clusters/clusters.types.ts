/**
 * Clusters types — facade over the codegen output.
 */

import type {
  AstroliftManagedDomain as GeneratedManagedDomain,
  AstroliftProviderPlugin as GeneratedProviderPlugin,
  AstroliftTenantCluster as GeneratedTenantCluster,
} from "@/graphql/__generated__/schema";

export type AstroliftGuid = string;

type ManagedDomainDefaultFor = "tenant_apps" | "preview_envs" | "both" | "none";

export type AstroliftTenantCluster = GeneratedTenantCluster;

export type AstroliftManagedDomain = Omit<
  GeneratedManagedDomain,
  "defaultFor"
> & {
  defaultFor: ManagedDomainDefaultFor;
};

export type AstroliftProviderPlugin = GeneratedProviderPlugin;
