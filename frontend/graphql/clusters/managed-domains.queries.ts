/**
 * Managed-domain queries.
 *
 * Re-exported from ``clusters.queries.ts`` so the settings surface
 * imports from a focused path (``managed-domains.queries``) while a
 * single ``gql`` node owns the operation registration. Defining the
 * same operation twice would trigger Apollo's duplicate-operation-name
 * warning and double the cache subscription churn on writes.
 */

export { LIST_MANAGED_DOMAINS } from "./clusters.queries";
