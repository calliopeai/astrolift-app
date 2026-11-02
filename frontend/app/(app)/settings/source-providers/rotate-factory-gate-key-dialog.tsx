"use client";

import { RotateFactoryGateKeySheet } from "@/components/screens/settings/source-providers/RotateFactoryGateKeySheet";
import { useRotateFactoryGateKey } from "@/components/screens/settings/source-providers/use-factory-gate-app";

/** Container: the rotate key mutation behind RotateFactoryGateKeySheet. */
export function RotateFactoryGateKeyDialog(props: {
  open: boolean;
  onOpenChange: (next: boolean) => void;
  owner: string | null;
}) {
  return <RotateFactoryGateKeySheet {...props} {...useRotateFactoryGateKey()} />;
}
