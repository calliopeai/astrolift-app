import type { InvitationAcceptProps, InvitationSummary } from "../auth/InvitationAccept";
import type { LoginScreenProps } from "../auth/LoginScreen";
import type { ActiveIdp } from "../auth/use-login";

import type { GlobalErrorScreenProps } from "./GlobalErrorScreen";
import type { PopoutTerminalScreenProps } from "./PopoutTerminalScreen";
import type { RootErrorScreenProps } from "./RootErrorScreen";

/** Hand-typed fixtures for the root, (bare) and (login) chrome (group root-bare-login). */

const noop = () => {};
const noopAsync = async () => {};

export const LONG_TEXT =
  "The upstream GraphQL gateway returned an unexpected response while resolving organization membership for this session, and the request could not be completed after several retries against every configured replica";

export const ROOT_ERROR: Omit<RootErrorScreenProps, "onReset"> = {
  message: "Cannot read properties of undefined (reading 'slug')",
  digest: "2841937611",
};

export const ROOT_ERROR_LONG: Omit<RootErrorScreenProps, "onReset"> = {
  message: LONG_TEXT,
  digest: "a8f3c1e9b2d74f6a8e0c5b1d9f3a7e2c4b6d8f0a1c3e5b7d9f1a3c5e7b9d1f3a",
};

export const GLOBAL_ERROR: Omit<GlobalErrorScreenProps, "onReset"> = ROOT_ERROR;
export const GLOBAL_ERROR_LONG: Omit<GlobalErrorScreenProps, "onReset"> = ROOT_ERROR_LONG;

export const POPOUT_TARGET: Omit<PopoutTerminalScreenProps, "terminal"> = {
  appSlug: "billing-api",
  podName: "billing-api-7d9f8c6b5-x2kq4",
  container: "api",
};

export const POPOUT_TARGET_LONG: Omit<PopoutTerminalScreenProps, "terminal"> = {
  appSlug: "platform-team-shared-production-workloads-billing-reconciliation-api",
  podName: "platform-team-shared-production-workloads-billing-reconciliation-api-7d9f8c6b5-x2kq4",
  container: "reconciliation-worker-with-a-deliberately-long-container-name",
};

export const IDP_EXTERNAL: ActiveIdp = {
  kind: "cognito",
  displayName: "Astrolift Cognito",
  loginPath: "/app/auth1/login",
  ready: true,
};

export const IDP_LOCAL: ActiveIdp = {
  kind: "local",
  displayName: "Local accounts",
  loginPath: null,
  ready: true,
};

export const IDP_NOT_READY: ActiveIdp = {
  kind: null,
  displayName: "",
  loginPath: null,
  ready: false,
};

export const LOGIN: LoginScreenProps = {
  devLogin: false,
  idp: IDP_EXTERNAL,
  notice: null,
  loadError: null,
  continueWithIdp: noop,
  switchAccount: noop,
  submitLocal: noopAsync,
};

export const INVITATION_SUMMARY: InvitationSummary = {
  organizationName: "Acme Robotics",
  inviterName: "Ada Lovelace",
  roleName: "Developer",
  emailHint: "n•••@acme.example",
  expiresAt: "2026-10-23T18:30:00Z",
};

export const INVITATION_SUMMARY_LONG: InvitationSummary = {
  organizationName:
    "Platform Team Shared Enterprise Production Workloads Organization With A Deliberately Long Name",
  inviterName: "Maximiliana Theodora Wolfeschlegelsteinhausenbergerdorff-Montgomery",
  roleName: "Organization Administrator With Every Permission Except Billing Changes",
  emailHint: "m•••@platform-team-shared-enterprise-production-workloads.example.com",
  expiresAt: "2026-10-23T18:30:00Z",
};

/** Signed out, on a pending invitation: the state a fresh invitee lands in. */
export const INVITATION: InvitationAcceptProps = {
  view: { kind: "signIn", invitation: INVITATION_SUMMARY },
  onSignIn: noop,
  onAccept: noop,
  onSignOut: noop,
  onRetry: noop,
  onGoToDashboard: noop,
};
