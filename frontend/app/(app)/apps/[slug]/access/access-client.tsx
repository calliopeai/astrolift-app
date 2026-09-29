"use client";

import { AppAccessScreen } from "@/components/screens/apps/security/AppAccessScreen";
import { useAppAccessTab } from "@/components/screens/apps/security/use-app-access-tab";

import { AppMembersClient } from "../members/app-members-client";
import { AccessCard } from "../security/access-card";
import { AppSecurityClient } from "../security/security-client";
import { AppDeployTokensClient } from "../tokens/tokens-client";

/**
 * The Access tab: the section in `?section=` and the viewer's permissions
 * from the hook, each section's own client as a slot. Only the active one
 * mounts, so only its queries run.
 */
export function AppAccessClient({ slug }: { slug: string }) {
  return (
    <AppAccessScreen
      {...useAppAccessTab()}
      slots={{
        members: <AppMembersClient slug={slug} />,
        tokens: <AppDeployTokensClient slug={slug} />,
        security: <AppSecurityClient slug={slug} edge={false} />,
        edge: <AccessCard appSlug={slug} />,
      }}
    />
  );
}
