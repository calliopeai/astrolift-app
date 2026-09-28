"use client";

import { CreatePolicyScreen } from "@/components/screens/administration/access/CreatePolicyScreen";
import { useCreatePolicy } from "@/components/screens/administration/access/use-create-policy";

/** Administration > Policies > New policy: the stepped create page. */
export function CreatePolicyClient() {
  return <CreatePolicyScreen {...useCreatePolicy()} />;
}
