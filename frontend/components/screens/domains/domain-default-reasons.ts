const reasons = {
  READ_ONLY_DOMAIN: "reasonReadOnly",
  DOMAIN_PROOF_PENDING: "reasonProof",
  DOMAIN_PROVISIONING_INCOMPLETE: "reasonProvisioning",
  DOMAIN_WRITER_UNAVAILABLE: "reasonWriter",
  DOMAIN_WRITER_CHANGED: "reasonWriter",
  ORGANIZATION_DEFAULT_OVERRIDE: "reasonOverride",
  ORGANIZATION_DEFAULT_TAKES_PRECEDENCE: "reasonPrecedence",
  PERMISSION_DENIED: "reasonPermission",
  FORBIDDEN: "reasonPermission",
  MANAGED_DOMAIN_VERSION_CHANGED: "reasonChanged",
  MANAGED_DOMAIN_DEFAULTS_CHANGED: "reasonChanged",
} as const;
export function domainDefaultReasonKey(reason: string) {
  return Object.hasOwn(reasons, reason) ? reasons[reason as keyof typeof reasons] : "unavailable";
}
