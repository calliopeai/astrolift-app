"use client";

import { RegisterClusterPage } from "@/components/screens/clusters/list/RegisterClusterPage";
import { useRegisterCluster } from "@/components/screens/clusters/list/use-register-cluster";

/** The register page wired to the provider catalog and the register mutation. */
export function RegisterClusterClient() {
  return <RegisterClusterPage {...useRegisterCluster()} cancelHref="/clusters" />;
}
