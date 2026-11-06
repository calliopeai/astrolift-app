"use client";

import type { AstroliftAppAccess } from "@/graphql/__generated__/schema";
import { Can } from "@/components/Can";
import { AccessCardView, AccessEditorView } from "@/components/screens/apps/security/AccessCard";
import { PublicSwitchView } from "@/components/screens/apps/security/PublicSwitch";
import { SecurityHeadersSwitchView } from "@/components/screens/apps/security/SecurityHeadersSwitch";
import {
  useAccessEditor,
  useAppAccess,
  usePublicSwitch,
} from "@/components/screens/apps/security/use-app-access";
import { useSecurityHeadersSwitch } from "@/components/screens/apps/security/use-security-headers-switch";

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
      securityHeadersSwitch={
        state.access ? (
          <Can
            permission="app.access"
            fallback={<SecurityHeadersSwitchView access={state.access} />}
          >
            <SecurityHeadersSwitch access={state.access} />
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

function SecurityHeadersSwitch({ access }: { access: AstroliftAppAccess }) {
  return <SecurityHeadersSwitchView access={access} control={useSecurityHeadersSwitch(access)} />;
}
