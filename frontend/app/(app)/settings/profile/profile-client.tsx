"use client";

import { ConnectedAccountsSection } from "@/components/ConnectedAccountsSection";
import { ProfilePreferences } from "@/components/screens/settings/account/ProfilePreferences";
import { useProfileTimezone } from "@/components/screens/settings/account/use-profile-timezone";
import { useConnectedAccounts } from "@/components/use-connected-accounts";
import { useLocaleSwitch } from "@/components/use-locale-switch";

/** Linked source accounts (#395), which only the old account drawer showed. */
export function ConnectedAccountsClient() {
  return <ConnectedAccountsSection {...useConnectedAccounts()} />;
}

export function AppearanceClient() {
  return <ProfilePreferences localeSwitch={useLocaleSwitch()} timezone={useProfileTimezone()} />;
}
