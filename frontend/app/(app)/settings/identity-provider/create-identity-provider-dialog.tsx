"use client";

import { CreateIdentityProviderSheet } from "@/components/screens/settings/identity-provider/CreateIdentityProviderSheet";
import { useCreateIdentityProvider } from "@/components/screens/settings/identity-provider/use-create-identity-provider";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/** Container for the "New provider" sheet: its create mutation lives in the hook. */
export function CreateIdentityProviderDialog({ open, onOpenChange }: Props) {
  return (
    <CreateIdentityProviderSheet
      {...useCreateIdentityProvider()}
      open={open}
      onOpenChange={onOpenChange}
    />
  );
}
