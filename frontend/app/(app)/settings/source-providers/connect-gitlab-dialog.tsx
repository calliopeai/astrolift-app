"use client";

import { ConnectGitLabDialogView } from "@/components/screens/settings/source-providers/ConnectGitLabDialog";
import { useConnectGitLab } from "@/components/screens/settings/source-providers/use-connect-gitlab";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/** Container: the connect mutation lives in the hook, the sheet in the view. */
export function ConnectGitLabDialog({ open, onOpenChange }: Props) {
  return (
    <ConnectGitLabDialogView {...useConnectGitLab()} open={open} onOpenChange={onOpenChange} />
  );
}
