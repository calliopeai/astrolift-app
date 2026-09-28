"use client";

import { GenerateSshKeySheet } from "@/components/screens/settings/source-providers/GenerateSshKeySheet";
import { useGenerateSshKey } from "@/components/screens/settings/source-providers/use-source-providers";

/** Container: the key-generation mutation behind GenerateSshKeySheet. */
export function GenerateSshKeyDialog(props: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  return <GenerateSshKeySheet {...props} {...useGenerateSshKey()} />;
}
