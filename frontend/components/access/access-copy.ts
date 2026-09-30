import type { PermissionPresentation, RoleRef, ScopeKind } from "./access-model";

const RESOURCE_KEYS = [
  "app",
  "agent",
  "agent_task",
  "agent_box",
  "agent_env_spec",
  "skill",
  "secret",
  "managed_service",
  "deploy_token",
  "form",
  "workflow",
  "pipeline",
  "org",
  "team",
  "project",
  "api_token",
  "webhook",
  "cluster",
  "provider_plugin",
  "scm",
  "audit_log",
  "billing",
  "zentinelle",
  "admin",
] as const;

type ResourceKey = (typeof RESOURCE_KEYS)[number];
type PresentationKey =
  | "presentation.none"
  | "presentation.everything"
  | "presentation.readOnly"
  | "presentation.resourceVerbs"
  | "presentation.bindReason"
  | `presentation.resource.${ResourceKey}`
  | `scope.${ScopeKind}`;
export type AccessTranslator = (
  key: PresentationKey,
  values?: Record<string, string | number>
) => string;
const resourceKeys = new Set<string>(RESOURCE_KEYS);
const isResourceKey = (resource: string): resource is ResourceKey => resourceKeys.has(resource);

export function localizedResourceLabel(resource: string, t: AccessTranslator): string {
  return isResourceKey(resource) ? t(`presentation.resource.${resource}`) : resource;
}

export function localizedPermissionPresentation(
  t: AccessTranslator,
  list: (items: string[]) => string
): PermissionPresentation {
  return {
    none: t("presentation.none"),
    everything: t("presentation.everything"),
    resource: (resource) => localizedResourceLabel(resource, t),
    list,
    readOnly: (resources) => t("presentation.readOnly", { resources }),
    resourceVerbs: (resource, verbs) => t("presentation.resourceVerbs", { resource, verbs }),
  };
}

export function localizedBindReason(t: AccessTranslator) {
  return (role: Pick<RoleRef, "name" | "scopeLevel">): string =>
    t("presentation.bindReason", { name: role.name, kind: t(`scope.${role.scopeLevel}`) });
}
