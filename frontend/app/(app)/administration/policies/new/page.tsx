import { routeTitle } from "@/lib/i18n/route-title";

import { CreatePolicyClient } from "./create-policy-client";

export const generateMetadata = routeTitle("newPolicy", "administration");

export default function NewPolicyPage() {
  return <CreatePolicyClient />;
}
