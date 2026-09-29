import { IDENTITY_PROVIDER_KINDS, type IdentityProviderKind } from "./identity-provider-kinds";

/** Hand-typed fixtures for the docs-c group (custom domains, identity providers). */

export const DOCS_C_KINDS_FULL: IdentityProviderKind[] = IDENTITY_PROVIDER_KINDS;

export const DOCS_C_KINDS_EMPTY: IdentityProviderKind[] = [];

const LONG =
  "an-identity-provider-with-a-deliberately-long-name-that-keeps-going-past-the-column-width-to-test-wrapping";

export const DOCS_C_KINDS_LONG: IdentityProviderKind[] = [
  {
    id: "long",
    title: `Enterprise federated SSO ${LONG}`,
    badge: "OIDC-FEDERATED-ENTERPRISE",
    intro: `A provider whose intro runs on and on ${LONG} to check that paragraphs wrap inside the article width.`,
    steps: [
      `Register a client at https://${LONG}.example.com/.well-known/openid-configuration and paste the redirect URI.`,
      `Copy the client_id ${LONG} and the client_secret.`,
      "In Astrolift, New provider · OIDC, paste the values, Set active.",
    ],
  },
];
