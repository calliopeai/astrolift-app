/**
 * Astrolift permission slugs.
 *
 * Mirror of `core/permissions.py:Permission`. Kept as string-literal
 * unions so:
 *   - the union narrows at compile time when you write a typo
 *   - it's serializable into Apollo cache as a flat string list
 *   - codegen (#265) can replace this with a generated source of
 *     truth without the call sites having to change
 *
 * Update both sides when adding a permission. The backend resolver
 * is the truth — when the union here drifts, the worst case is a UI
 * that hides an action that's actually allowed (recoverable in the
 * next deploy).
 */

export type AstroliftPermission =
  // Org / team / project / membership
  | "org.read"
  | "org.update"
  | "org.delete"
  | "org.manage_members"
  | "team.read"
  | "team.create"
  | "team.update"
  | "team.delete"
  | "team.manage_members"
  | "project.read"
  | "project.create"
  | "project.update"
  | "project.delete"
  // Apps + lifecycle
  | "app.read"
  | "app.create"
  | "app.update"
  | "app.delete"
  | "app.transfer"
  | "app.deploy"
  | "app.rollback"
  | "app.approve_deploy"
  | "app.read_logs"
  | "app.read_metrics"
  | "app.exec_pod"
  // Secrets
  | "secret.read"
  | "secret.write"
  | "secret.list"
  | "secret.approve"
  // Managed services
  | "managed_service.create"
  | "managed_service.update"
  | "managed_service.destroy"
  // Tokens
  | "deploy_token.create"
  | "deploy_token.rotate"
  | "deploy_token.revoke"
  | "api_token.create"
  | "api_token.revoke"
  // Webhooks
  | "webhook.create"
  | "webhook.update"
  | "webhook.delete"
  // Operations
  | "audit_log.read"
  | "billing.read"
  | "billing.update"
  // Clusters / providers
  | "cluster.register"
  | "cluster.update"
  | "cluster.unregister"
  | "cluster.manage"
  | "provider_plugin.read"
  // SCM integration
  | "scm.read"
  | "scm.connect"
  | "scm.disconnect"
  | "scm.key_create"
  | "scm.key_delete";

export type PermissionCheck =
  | AstroliftPermission
  | { anyOf: AstroliftPermission[] }
  | { allOf: AstroliftPermission[] };

export function permissionMatches(granted: ReadonlySet<string>, check: PermissionCheck): boolean {
  if (typeof check === "string") return granted.has(check);
  if ("anyOf" in check) return check.anyOf.some((p) => granted.has(p));
  if ("allOf" in check) return check.allOf.every((p) => granted.has(p));
  return false;
}
