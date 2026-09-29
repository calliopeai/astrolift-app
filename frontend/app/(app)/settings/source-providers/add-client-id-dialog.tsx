"use client";

import { AddClientIdSheet } from "@/components/screens/settings/source-providers/AddClientIdSheet";
import { useAddClientId } from "@/components/screens/settings/source-providers/use-source-providers";
import type { AstroliftSourceConnection } from "@/graphql/scm/scm.types";

/** Container: the Client ID mutation behind AddClientIdSheet. */
export function AddClientIdDialog(props: {
  connection: AstroliftSourceConnection | null;
  onClose: () => void;
}) {
  return <AddClientIdSheet {...props} {...useAddClientId()} />;
}
