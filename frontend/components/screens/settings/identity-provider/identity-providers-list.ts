/**
 * Providers › Identity (spec 44 §5.1): the embedded list's declaration and
 * its client-side step. `astroliftIdentityProviders` returns every provider
 * at once with no arguments, so search, the filters, sort and numbered
 * pages run in the client (needsBackend: a Page field). Providers record no
 * creator, so Mine is empty.
 */
import type { SelectRowsSpec } from "@/components/list/select-rows";
import { type ListDefinition, standardViews } from "@/components/list/use-list-state";
import type { AstroliftIdentityProvider } from "@/graphql/identity/identity.types";

const KINDS = ["oidc", "saml", "cognito", "auth0", "okta", "azure_ad", "google", "github", "local"];

export const IDENTITY_PROVIDERS_LIST: ListDefinition = {
  id: "providers.identity",
  fields: [
    { key: "kind", label: "Kind", options: KINDS.map((k) => ({ value: k, label: k })) },
    {
      key: "status",
      label: "Status",
      options: [
        { value: "active", label: "active" },
        { value: "configured", label: "configured" },
      ],
    },
  ],
  searchPlaceholder: "Search providers, endpoints, client ids…",
  defaultSort: [{ key: "name", dir: "asc" }],
  views: standardViews({ createdBy: "me" }, [], {
    mineNote: "Identity providers do not record who configured them yet, so Mine is empty.",
  }),
  paging: "numbered",
  pageSizes: [25, 50, 100],
};

export const IDENTITY_PROVIDERS_SELECT: SelectRowsSpec<AstroliftIdentityProvider> = {
  filter: {
    createdBy: () => false,
    kind: (p, v) => p.kind === v,
    status: (p, v) => (p.isActive ? "active" : "configured") === v,
  },
  text: (p) => [p.name, p.clientId, p.oidcDiscoveryUrl, p.metadataUrl],
  sort: {
    // The active provider first, then by name.
    name: (p) => `${p.isActive ? 0 : 1}${p.name.toLowerCase()}`,
    kind: (p) => p.kind,
  },
  id: (p) => p.id,
};
