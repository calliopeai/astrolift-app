import type { AstroliftTemporaryAppPolicy } from "@/graphql/__generated__/schema";
import { parseToml, type TomlTable } from "@/lib/manifest/toml";

export interface TemporaryAppValues {
  expiresIn?: string;
  deleteDataOnExpiry?: boolean;
}

export interface TemporaryAppRules {
  policy: AstroliftTemporaryAppPolicy;
  permanentAllowed: boolean;
}

export function durationSeconds(value: string): number | null {
  const match = /^(\d{1,6})(mo|m|h|d|w)$/.exec(value.trim().toLowerCase());
  if (!match) return null;
  const units: Record<string, number> = { m: 60, h: 3600, d: 86400, w: 604800, mo: 2592000 };
  const seconds = Number(match[1]) * units[match[2]];
  return seconds > 0 ? seconds : null;
}

export function manifestExpiry(raw: string): string {
  try {
    const parsed = parseToml(raw);
    const app = parsed.app as TomlTable | undefined;
    return typeof app?.expires_in === "string" ? app.expires_in : "";
  } catch {
    return "";
  }
}

export function temporaryAppValid(
  values: TemporaryAppValues,
  rules: TemporaryAppRules | null,
  inherited = ""
): boolean {
  const ttl = values.expiresIn?.trim() || inherited.trim();
  if (!rules) return !values.expiresIn?.trim() && !values.deleteDataOnExpiry;
  if (!ttl) return rules.permanentAllowed;
  const seconds = durationSeconds(ttl);
  return (
    seconds !== null &&
    seconds >= rules.policy.minTtlSeconds &&
    seconds <= rules.policy.maxTtlSeconds
  );
}

export function temporaryAppInput(values: TemporaryAppValues, inherited = "") {
  const expiresIn = values.expiresIn?.trim() || null;
  return {
    expiresIn,
    expiresAt: null,
    deleteDataOnExpiry: Boolean((expiresIn || inherited) && values.deleteDataOnExpiry),
  };
}

export interface TemporaryAppPolicyState {
  rules: TemporaryAppRules | null;
  loading: boolean;
  failed: boolean;
  onRetry?: () => void;
}
