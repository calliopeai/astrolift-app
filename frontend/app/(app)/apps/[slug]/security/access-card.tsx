"use client";

import type { AstroliftAppAccess } from "@/graphql/__generated__/schema";
import { AccessCardView, AccessEditorView } from "@/components/screens/apps/security/AccessCard";
import { useAccessEditor, useAppAccess } from "@/components/screens/apps/security/use-app-access";

/**
 * Who may enter this app behind central auth (#2132). The editor gets its
 * own container so its preview query and save run only when it is shown.
 */
export function AccessCard({ appSlug }: { appSlug: string }) {
  const state = useAppAccess(appSlug);
  return (
    <AccessCardView
      {...state}
      editor={state.access ? <AccessEditor access={state.access} /> : null}
    />
  );
}

function AccessEditor({ access }: { access: AstroliftAppAccess }) {
  return <AccessEditorView {...useAccessEditor(access)} />;
}
