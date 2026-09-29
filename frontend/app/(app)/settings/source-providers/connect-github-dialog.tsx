"use client";

import { ConnectGitHubDialogView } from "@/components/screens/settings/source-providers/ConnectGitHubDialog";
import { useConnectGitHub } from "@/components/screens/settings/source-providers/use-connect-github";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/** Container: the manifest hand-off and adopt mutation live in the hook, the sheet in the view. */
export function ConnectGitHubDialog({ open, onOpenChange }: Props) {
  return (
    <ConnectGitHubDialogView {...useConnectGitHub()} open={open} onOpenChange={onOpenChange} />
  );
}
