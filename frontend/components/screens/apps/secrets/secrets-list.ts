/**
 * The app's Secrets tab as two lists, one per section (Leo's list rules 3
 * and 4): the secret keys (the default section) and the secret bundles
 * attached to the app (`?section=bundles`).
 *
 * `astroliftAppSecrets` and `astroliftAppSecretBundleAttachments` return
 * every row for the environment at once, with no filter, sort or page, so
 * the hook reads the whole set and these answer the rest in the browser,
 * with numbered pages and each view's note saying so. Pure.
 */
import type { ListDefinition } from "@/components/list/use-list-state";

import { CLIENT_LIST_NOTE, type ClientListSpec, selectClientRows } from "../client-list";
import type { AppSecret, AppSecretBundleAttachment } from "./secrets.types";

/** The Secrets tab's sections, in the order its nav lists them. */
export const SECRETS_SECTIONS = ["keys", "bundles"] as const;
export type SecretsSection = (typeof SECRETS_SECTIONS)[number];

/** `?section=bundles` is the bundles section; anything else is the keys. */
export function secretsSection(raw: string | string[] | undefined | null): SecretsSection {
  return raw === "bundles" ? "bundles" : "keys";
}

export const APP_SECRETS_LIST: ListDefinition = {
  id: "apps.secrets",
  fields: [
    {
      key: "source",
      label: "Source",
      options: [
        { value: "literal", label: "Literal" },
        { value: "bundle", label: "Bundle" },
        { value: "managed_service", label: "Managed service" },
      ],
    },
    {
      key: "scope",
      label: "Scope",
      options: [
        { value: "all", label: "All deploys" },
        { value: "production", label: "Production" },
        { value: "preview", label: "Previews" },
      ],
    },
  ],
  searchPlaceholder: "Search keys…",
  defaultSort: [{ key: "key", dir: "asc" }],
  // A secret has no owner of its own, so there is no Mine: one view, and
  // the list draws no view picker.
  views: [{ key: "all", label: "All", filters: {}, note: CLIENT_LIST_NOTE }],
  paging: "numbered",
  pageSizes: [25, 50, 100],
};

const SECRETS_SPEC: ClientListSpec<AppSecret> = {
  matches: (s, key, value) => {
    if (key === "source") return s.source === value;
    if (key === "scope") {
      const scope = s.scope || "all";
      return value === "preview" ? scope.startsWith("preview") : scope === value;
    }
    return true;
  },
  text: (s) => [s.key, s.environmentName, s.bundleSlug, s.managedServiceKind],
  compare: (a, b, key) => {
    switch (key) {
      case "env":
        return a.environmentName.localeCompare(b.environmentName);
      case "edited":
        return (Date.parse(a.lastEditedAt ?? "") || 0) - (Date.parse(b.lastEditedAt ?? "") || 0);
      case "key":
      default:
        return a.key.localeCompare(b.key);
    }
  },
  tie: (a, b) => a.id.localeCompare(b.id),
};

export function selectSecrets(
  all: AppSecret[],
  filters: Record<string, string>,
  state: Parameters<typeof selectClientRows>[2]
) {
  return selectClientRows(all, filters, state, SECRETS_SPEC);
}

export const APP_SECRET_BUNDLES_LIST: ListDefinition = {
  id: "apps.secrets.bundles",
  fields: [],
  searchPlaceholder: "Search bundles, teams, prefixes…",
  defaultSort: [{ key: "order", dir: "asc" }],
  views: [{ key: "all", label: "All", filters: {}, note: CLIENT_LIST_NOTE }],
  paging: "numbered",
  pageSizes: [25, 50, 100],
};

const BUNDLES_SPEC: ClientListSpec<AppSecretBundleAttachment> = {
  matches: () => true,
  text: (a) => [a.bundleName, a.bundleSlug, a.teamSlug, a.prefix, a.environmentName],
  compare: (a, b, key) => {
    switch (key) {
      case "bundle":
        return a.bundleName.localeCompare(b.bundleName);
      case "attached":
        return (Date.parse(a.attachedAt ?? "") || 0) - (Date.parse(b.attachedAt ?? "") || 0);
      case "order":
      default:
        return a.mergeOrder - b.mergeOrder;
    }
  },
  tie: (a, b) => a.id.localeCompare(b.id),
};

export function selectBundles(
  all: AppSecretBundleAttachment[],
  filters: Record<string, string>,
  state: Parameters<typeof selectClientRows>[2]
) {
  return selectClientRows(all, filters, state, BUNDLES_SPEC);
}
