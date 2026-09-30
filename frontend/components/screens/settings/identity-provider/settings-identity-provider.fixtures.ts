/**
 * Hand-typed story fixtures for the identity-provider settings screens,
 * typed against each view's props so a story cannot drift from its hook.
 */
import type { AstroliftIdentityProvider } from "@/graphql/identity/identity.types";

import type { CreateIdentityProviderSheetProps } from "./CreateIdentityProviderSheet";
import type { IdentityProvidersScreenProps } from "./IdentityProvidersScreen";

/** The JSON scalar is typed as a record; real values are any JSON. */
const json = (value: unknown) => value as AstroliftIdentityProvider["config"];

const base = {
  organizationSlug: "acme",
  isDefault: false,
  version: 1,
  createdAt: "2026-06-01T09:00:00Z",
  updatedAt: "2026-09-20T14:30:00Z",
  activatedAt: null,
  lastSwitchedByUsername: null,
};

export const AUTH0: AstroliftIdentityProvider = {
  ...base,
  id: "idp-7f3c2a91-auth0",
  kind: "auth0",
  name: "Auth0 prod",
  isActive: true,
  clientId: "Xk29sLq0PzT4vR8mNc1YwE",
  oidcDiscoveryUrl: "https://acme.us.auth0.com/.well-known/openid-configuration",
  metadataUrl: "",
  config: json({}),
  activatedAt: "2026-08-14T10:05:00Z",
  lastSwitchedByUsername: "ada.lovelace",
};

export const COGNITO: AstroliftIdentityProvider = {
  ...base,
  id: "idp-1d2e3f4a-cognito",
  kind: "cognito",
  name: "Cognito staging",
  isActive: false,
  clientId: "3n4b5v6c7x8z9a0s1d2f3g4h5j",
  oidcDiscoveryUrl:
    "https://cognito-idp.us-east-2.amazonaws.com/us-east-2_AbCdEfGhI/.well-known/openid-configuration",
  metadataUrl: "",
  config: json({ user_pool_id: "us-east-2_AbCdEfGhI", region: "us-east-2" }),
};

export const SAML: AstroliftIdentityProvider = {
  ...base,
  id: "idp-9c2d41e0-saml",
  kind: "saml",
  name: "SAML 2.0",
  isActive: false,
  clientId: "",
  oidcDiscoveryUrl: "",
  metadataUrl: "https://idp.example.com/metadata",
  config: json({}),
};

export const LOCAL: AstroliftIdentityProvider = {
  ...base,
  id: "idp-5b6c7d8e-local",
  kind: "local",
  name: "Local accounts",
  isActive: false,
  clientId: "",
  oidcDiscoveryUrl: "",
  metadataUrl: "",
  config: json(null),
};

const noop = async () => {};

export const SCREEN: Omit<IdentityProvidersScreenProps, "renderCreateSheet"> = {
  providers: [AUTH0, COGNITO, SAML, LOCAL],
  loading: false,
  error: undefined,
  canManageIdp: true,
  switching: false,
  deleting: false,
  onSetActive: noop,
  onDelete: noop,
};

const LONG =
  "Acme Corporation global workforce single sign-on (production, us-east-2, managed by platform security)";

export const LONG_PROVIDERS: AstroliftIdentityProvider[] = [
  {
    ...AUTH0,
    name: LONG,
    oidcDiscoveryUrl: `https://${"very-long-tenant-subdomain-".repeat(3)}acme.us.auth0.com/.well-known/openid-configuration`,
    lastSwitchedByUsername: "a.very.long.username.from.the.directory@subsidiary.example.com",
  },
  {
    ...COGNITO,
    kind: "custom_enterprise_federation_kind" as AstroliftIdentityProvider["kind"],
    name: LONG,
  },
];

export const LOAD_ERROR = new Error(
  "Response not successful: Received status code 503 (upstream identity service unavailable)"
);

export const CREATE_SHEET: CreateIdentityProviderSheetProps = {
  open: true,
  onOpenChange: () => {},
  onCreate: async () => true,
  creating: false,
};
