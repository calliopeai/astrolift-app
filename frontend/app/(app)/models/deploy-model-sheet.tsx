"use client";

import { DeployModelSheetView } from "@/components/screens/models/DeployModelSheet";
import { useDeployModel } from "@/components/screens/models/use-deploy-model";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onDeployed: () => void;
}

/** The Deploy model sheet; its queries run only while it is open. */
export function DeployModelSheet({ open, onOpenChange, onDeployed }: Props) {
  return (
    <DeployModelSheetView
      open={open}
      onOpenChange={onOpenChange}
      onDeployed={onDeployed}
      {...useDeployModel(open)}
    />
  );
}
