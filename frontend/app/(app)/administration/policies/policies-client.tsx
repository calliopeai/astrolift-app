"use client";

import { PoliciesScreen } from "@/components/screens/administration/access/PoliciesScreen";
import { usePolicies } from "@/components/screens/administration/access/use-policies";

/** Administration > Policies, wired to the policy walk and mutations. */
export function PoliciesClient() {
  return <PoliciesScreen {...usePolicies()} />;
}
