"use client";

import type { AstroliftAppAccess } from "@/graphql/__generated__/schema";
import { Can } from "@/components/Can";
import { AccessCardView, AccessEditorView } from "@/components/screens/apps/security/AccessCard";
import { CustomDomainsView } from "@/components/screens/apps/security/CustomDomains";
import { PublicSwitchView } from "@/components/screens/apps/security/PublicSwitch";
import { SecurityHeadersSwitchView } from "@/components/screens/apps/security/SecurityHeadersSwitch";
import {
  useAccessEditor,
  useAppAccess,
  useCustomDomainControl,
  useCustomDomains,
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
      customDomains={state.access ? <CustomDomains appSlug={appSlug} /> : null}
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

function CustomDomains({ appSlug }: { appSlug: string }) {
  const state = useCustomDomains(appSlug);
  return (
    <Can permission="app.access" fallback={<CustomDomainsView {...state} />}>
      <CustomDomainsControlled appSlug={appSlug} state={state} />
    </Can>
  );
}

function CustomDomainsControlled({
  appSlug,
  state,
}: {
  appSlug: string;
  state: ReturnType<typeof useCustomDomains>;
}) {
  return <CustomDomainsView {...state} control={useCustomDomainControl(appSlug)} />;
}

function SecurityHeadersSwitch({ access }: { access: AstroliftAppAccess }) {
  return <SecurityHeadersSwitchView access={access} control={useSecurityHeadersSwitch(access)} />;
}
