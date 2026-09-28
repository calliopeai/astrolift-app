"use client";

import { StartDeploymentSheet } from "@/components/screens/deployments/StartDeploymentSheet";
import { useStartDeployment } from "@/components/screens/deployments/use-start-deployment";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function StartDeploymentDialog({ open, onOpenChange }: Props) {
  return (
    <StartDeploymentSheet {...useStartDeployment(open)} open={open} onOpenChange={onOpenChange} />
  );
}
