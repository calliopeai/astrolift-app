"use client";

import type { AstroliftAppAccess } from "@/graphql/__generated__/schema";
import { Can } from "@/components/Can";
import { AccessCardView, AccessEditorView } from "@/components/screens/apps/security/AccessCard";
import { PublicSwitchView } from "@/components/screens/apps/security/PublicSwitch";
import {
  useAccessEditor,
  useAppAccess,
  usePublicSwitch,
} from "@/components/screens/apps/security/use-app-access";

/**
 * Who may enter this app behind central auth (#2132), and whether its
 * platform URL skips the login at all (#2504). The editor and the switch
 * get their own containers so their queries and saves run only when shown.
 */
export function AccessCard({ appSlug }: { appSlug: string }) {
  const state = useAppAccess(appSlug);
  return (
    <AccessCardView
      {...state}
      editor={state.access ? <AccessEditor access={state.access} /> : null}
      publicSwitch={
        state.access ? (
          <Can permission="app.access" fallback={<PublicSwitchView access={state.access} />}>
            <PublicSwitch access={state.access} />
          </Can>
        ) : null
      }
    />
  );
}

function AccessEditor({ access }: { access: AstroliftAppAccess }) {
  return <AccessEditorView {...useAccessEditor(access)} />;
}

function PublicSwitch({ access }: { access: AstroliftAppAccess }) {
  return <PublicSwitchView access={access} control={usePublicSwitch(access)} />;
}
