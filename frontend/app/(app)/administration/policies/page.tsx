import { PoliciesClient } from "./policies-client";

export const metadata = { title: "Policies · Administration · Astrolift" };

/**
 * The list reads `astroliftPoliciesPage` with its own search and cursor; it
 * preloaded the flat `astroliftPolicies` list it never shows, a second fetch
 * for nothing (Leo's page rule 2), so the page renders the client alone.
 */
export default function PoliciesAdministrationPage() {
  return <PoliciesClient />;
}
