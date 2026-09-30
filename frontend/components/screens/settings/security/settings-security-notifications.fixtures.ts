import type { AstroliftActiveSession } from "@/graphql/identity/identity.types";
import type { AstroliftUserAlertSubscription } from "@/graphql/lifecycle/lifecycle.types";
import type { AstroliftNotification } from "@/graphql/operations/operations.types";
import type { AstroliftRegisteredApp } from "@/graphql/registry/registry.types";

import { subKey } from "./alert-kinds";
import type { AlertSubscriptionsViewProps } from "./AlertSubscriptions";
import type { NotificationsInboxProps } from "./NotificationsInbox";
import type { PairDeviceViewProps } from "./PairDevice";
import type { SecuritySettingsViewProps } from "./SecuritySettings";

/** Hand-typed fixtures for Settings > Security and Settings > Notifications. */

const noop = () => {};
const resolved = async () => {};
const yes = () => true;
const resolvedTrue = async () => true;

export const LONG =
  "platform-team-shared-production-workloads-us-west-2-with-a-deliberately-long-name-that-keeps-going";

// Relative to load time so "Last seen" and the QR countdown read naturally.
const NOW = Date.now();
const ago = (ms: number) => new Date(NOW - ms).toISOString();
const ahead = (ms: number) => new Date(NOW + ms).toISOString();
const MIN = 60_000;
const DAY = 24 * 60 * MIN;

// ---- Security: sessions ----------------------------------------------------

function session(over: Partial<AstroliftActiveSession>): AstroliftActiveSession {
  return {
    id: "sess-1",
    clientKind: "web",
    label: "",
    isCurrent: false,
    attestationKind: "none",
    attestationTrustLevel: "none",
    attestedAt: null,
    createdAt: ago(3 * DAY),
    elevatedUntil: null,
    elevationMethod: null,
    expiresAt: ahead(27 * DAY),
    ipAddress: "203.0.113.7",
    lastSeenAt: ago(2 * MIN),
    userAgent: "Mozilla/5.0",
    ...over,
  };
}

export const SESSIONS: AstroliftActiveSession[] = [
  session({
    id: "sess-web",
    clientKind: "web",
    label: "Chrome on macOS",
    isCurrent: true,
    lastSeenAt: ago(1000),
  }),
  session({
    id: "sess-cli",
    clientKind: "cli",
    label: "astro 3.3.1 on leo-mbp",
    lastSeenAt: ago(40 * MIN),
  }),
  session({
    id: "sess-mobile",
    clientKind: "mobile",
    label: "Sarah's iPhone",
    lastSeenAt: ago(5 * DAY),
  }),
  session({
    id: "sess-ext",
    clientKind: "browser_extension",
    label: "",
    lastSeenAt: null,
    expiresAt: null,
  }),
  session({
    id: "sess-token",
    clientKind: "api_token",
    label: "ci-deploy",
    lastSeenAt: ago(400 * DAY),
  }),
];

export function securityProps(
  over: Partial<SecuritySettingsViewProps> = {}
): SecuritySettingsViewProps {
  const sessions = over.sessions ?? SESSIONS;
  return {
    sessions,
    otherCount: sessions.filter((s) => !s.isCurrent).length,
    loading: false,
    errorMessage: null,
    signingOut: false,
    revoking: false,
    onRequestSignOutAll: yes,
    onSignOutAll: resolved,
    onRevoke: resolvedTrue,
    ...over,
  };
}

export const SECURITY_LONG_SESSIONS: AstroliftActiveSession[] = [
  session({ id: "sess-web", clientKind: "web", label: LONG, isCurrent: true }),
  session({ id: "sess-cli", clientKind: "cli", label: `${LONG}-${LONG}` }),
];

// ---- Security: pair device -------------------------------------------------

// A small stand-in for the server-rendered SVG: the real one is a QR matrix.
const QR_SVG =
  '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 8 8" width="256" height="256"><rect width="8" height="8" fill="white"/><rect x="0" y="0" width="3" height="3"/><rect x="5" y="0" width="3" height="3"/><rect x="0" y="5" width="3" height="3"/><rect x="4" y="4" width="1" height="1"/><rect x="6" y="6" width="1" height="1"/></svg>';

export const QR_PAYLOAD = {
  expiresAt: ahead(4 * MIN + 30_000),
  qrPayload: "astrolift://enroll?install=conflict&token=enr_7f3c2a91e11b0c4",
  qrSvg: QR_SVG,
  sessionGuid: "0b6f7d52-4c1e-4e0a-9d77-3f1b8f6d2a10",
  sessionId: "ses_7f3c2a91e11b0c4d02e5f7",
  verificationUri: "https://astrolift.example.com/device?code=K7QX-2MPD",
};

export function pairProps(over: Partial<PairDeviceViewProps> = {}): PairDeviceViewProps {
  return {
    payload: null,
    loading: false,
    onMint: resolved,
    onCopyVerificationUri: resolved,
    onCopyPayload: resolved,
    onClear: noop,
    ...over,
  };
}

export const QR_EXPIRED = { ...QR_PAYLOAD, expiresAt: ago(MIN) };

export const QR_LONG = {
  ...QR_PAYLOAD,
  sessionId: `ses_${LONG}`,
  verificationUri: `https://${LONG}.example.com/device?code=K7QX-2MPD&install=${LONG}`,
};

// ---- Notifications: alert subscriptions -----------------------------------

// Only the fields the matrix reads; the full registered-app shape is large.
const app = (id: string, name: string, slug: string) =>
  ({ id, name, slug }) as unknown as AstroliftRegisteredApp;

export const APPS: AstroliftRegisteredApp[] = [
  app("app-1", "Checkout", "checkout"),
  app("app-2", "Search API", "search-api"),
  app("app-3", "Marketing site", "marketing-site"),
];

function sub(appSlug: string, alertKind: string, enabled = true): AstroliftUserAlertSubscription {
  return { id: `sub-${appSlug}-${alertKind}`, appSlug, alertKind, channel: "web", enabled };
}

const SUBS: AstroliftUserAlertSubscription[] = [
  ...[
    "deploy_success",
    "deploy_failure",
    "error_spike",
    "preview_created",
    "preview_destroyed",
  ].map((k) => sub("checkout", k)),
  sub("search-api", "deploy_failure"),
  sub("search-api", "error_spike", false),
];

function subMapOf(list: AstroliftUserAlertSubscription[]) {
  return new Map(list.map((s) => [subKey(s.appSlug, s.alertKind), s]));
}

/** The view's props less the list controller, which the story builds. */
export function alertProps(
  over: Partial<Omit<AlertSubscriptionsViewProps, "list">> = {}
): Omit<AlertSubscriptionsViewProps, "list"> {
  return {
    rows: APPS,
    totalCount: APPS.length,
    nextCursor: null,
    loading: false,
    error: null,
    onRetry: () => {},
    subMap: subMapOf(SUBS),
    busy: false,
    onToggle: resolved,
    onSubscribeAll: resolved,
    onUnsubscribeAll: resolved,
    ...over,
  };
}

export const LONG_APPS: AstroliftRegisteredApp[] = [app("app-long", `Production ${LONG}`, LONG)];

// ---- Notifications: inbox --------------------------------------------------

function notification(over: Partial<AstroliftNotification>): AstroliftNotification {
  return {
    id: "n-1",
    kind: "deploy_failure",
    title: "Deploy failed",
    body: "",
    link: "",
    createdAt: ago(5 * MIN),
    readAt: null,
    userId: "u-1",
    ...over,
  };
}

export const NOTIFICATIONS: AstroliftNotification[] = [
  notification({
    id: "n-1",
    kind: "deploy_failure",
    title: "checkout: deploy failed",
    body: "Release 7f3c2a91 failed its readiness probe on prod-us-west-2.",
  }),
  notification({
    id: "n-2",
    kind: "deploy_approval",
    title: "search-api: deploy awaiting approval",
    body: "Maria requested a production deploy of 1.14.2.",
    createdAt: ago(40 * MIN),
  }),
  notification({
    id: "n-3",
    kind: "invitation",
    title: "You were added to Platform team",
    createdAt: ago(2 * DAY),
    readAt: ago(DAY),
  }),
  notification({
    id: "n-4",
    kind: "quota_warning",
    title: "Org is at 90% of its app quota",
    body: "18 of 20 apps used.",
    createdAt: ago(4 * DAY),
    readAt: ago(3 * DAY),
  }),
];

export const LONG_NOTIFICATIONS: AstroliftNotification[] = [
  notification({
    id: "n-long-1",
    kind: "deploy_failure_with_a_very_long_kind_name",
    title: `${LONG}: deploy failed`,
    body: `Release ${LONG} failed its readiness probe after 5 attempts; the container exited with code 137 (out of memory) on node ip-10-0-12-34.${LONG}.`,
  }),
  notification({ id: "n-long-2", title: LONG, readAt: ago(DAY) }),
];

export function inboxProps(over: Partial<NotificationsInboxProps> = {}): NotificationsInboxProps {
  return {
    notifications: NOTIFICATIONS,
    loading: false,
    marking: false,
    markingAll: false,
    onMarkRead: resolved,
    onMarkAll: resolved,
    ...over,
  };
}
