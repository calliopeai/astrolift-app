"use client";

import { ConnectSourceDialogView } from "@/components/screens/settings/source-providers/ConnectSourceDialog";
import { useConnectSource } from "@/components/screens/settings/source-providers/use-connect-source";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/** Container: the connect mutation lives in the hook, the sheet in the view. */
export function ConnectSourceDialog({ open, onOpenChange }: Props) {
  return (
    <ConnectSourceDialogView {...useConnectSource()} open={open} onOpenChange={onOpenChange} />
  );
}
