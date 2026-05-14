/**
 * Canonical in-app documentation routes.
 *
 * The 5 operator doc pages (custom-domains, source-providers,
 * identity-providers, policies, webhooks) ship from
 * `app/(app)/documentation/`. Every "Learn more" button in the product
 * should route through this map so that when a doc page is renamed or
 * moved, the in-app links migrate in lockstep.
 *
 * For external references (third-party SCM provider docs, hosted MkDocs
 * pages once the public docs site is live), keep the URL inline at the
 * call site — those have nothing to do with the in-app router.
 */

export const DOC_LINKS = {
  customDomains: "/documentation/custom-domains",
  sourceProviders: "/documentation/source-providers",
  identityProviders: "/documentation/identity-providers",
  policies: "/documentation/policies",
  webhooks: "/documentation/webhooks",
  clusterPrerequisites: "/documentation/cluster-prerequisites",
  configuration: "/documentation/configuration",
  webhookEvents: "/documentation/webhook-events",
  introduction: "/documentation/introduction",
  getStarted: "/documentation/get-started",
  tutorials: "/documentation/tutorials",
  changelog: "/documentation/changelog",
} as const satisfies Record<string, `/documentation/${string}`>;

export type DocLinkKey = keyof typeof DOC_LINKS;
