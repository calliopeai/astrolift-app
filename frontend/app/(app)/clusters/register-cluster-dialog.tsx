"use client";

import { RegisterClusterSheet } from "@/components/screens/clusters/list/RegisterClusterSheet";
import { useRegisterCluster } from "@/components/screens/clusters/list/use-register-cluster";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/** The register sheet wired to the provider catalog and the register mutation. */
export function RegisterClusterDialog({ open, onOpenChange }: Props) {
  return <RegisterClusterSheet {...useRegisterCluster()} open={open} onOpenChange={onOpenChange} />;
}
