/** Row shapes for the app Secrets and Deploy tokens tabs. */

export interface SecretEditor {
  id: string;
  username: string;
  displayName: string;
}

export interface AppSecret {
  id: string;
  key: string;
  environmentName: string;
  source: string;
  bundleSlug: string;
  managedServiceKind: string;
  isMasked: boolean;
  lastEditedAt?: string | null;
  lastEditedBy?: SecretEditor | null;
  // #677 / #678 — sidecar metadata. expiresAt is null when no
  // explicit rotation deadline; setVia falls back to "web" for rows
  // the platform has never tagged.
  expiresAt?: string | null;
  setVia?: string;
  // #679 — deploy-target scope. "all" | "production" | "preview" |
  // "preview:<branch>". Optional in the local type because the
  // LIST_APP_SECRETS query selection set may not include scope on
  // older builds; treat missing/empty as "all".
  scope?: string | null;
  // #1923 — false for a literal row whose staged value has no matching
  // applied secret-change proposal under an app that requires approval:
  // shown here, but not what the next deploy actually puts in front of a
  // workload (it reverts to the last-approved value instead). Optional /
  // defaults true so older builds that predate this field render as before.
  deploysAsShown?: boolean | null;
}

export interface AppSecretBundleAttachment {
  id: string;
  registeredAppSlug: string;
  environmentName: string;
  bundleSlug: string;
  bundleName: string;
  prefix: string;
  teamSlug?: string | null;
  keyCount: number;
  mergeOrder: number;
  attachedAt?: string | null;
}

export interface RevealedSecretData {
  secretId: string;
  key: string;
  environmentName: string;
  value: string;
  revealedAt: string;
}

export interface SecretHistoryEntry {
  timestamp: string;
  action: string;
  success: boolean;
  errorCode: string | null;
  sourceIp: string | null;
  actor: { id: string; username: string } | null;
}

export interface DeployToken {
  id: string;
  name: string;
  last4: string;
  scopes: string[];
  expiresAt?: string | null;
  lastUsedAt?: string | null;
  /** Last client IP that authed with this token. Empty string when never used.
   *  Populated by the deploy-token middleware (#425). */
  lastUsedIp: string;
  /** Last User-Agent that authed with this token. Empty string when never used. */
  lastUsedAgent: string;
  isRevoked: boolean;
  lastRotatedAt?: string | null;
  registeredAppSlug: string;
  createdAt: string;
}

export interface DeployTokenSecretReveal {
  token: DeployToken;
  plaintextSecret: string;
  /** Grace window (seconds) the previous secret stays valid after rotation.
   *  ``0`` on creation (no previous secret to honour). Sourced live from the
   *  backend's ``DEPLOY_TOKEN_ROTATION_GRACE_SECONDS`` Constance entry so the
   *  rotate-confirm dialog can display the actual operator-set value (#425). */
  rotationGraceSeconds: number;
}

export interface CreateDeployTokenInput {
  name: string;
  scopes: string[] | null;
  expiresAtIso: string | null;
}
