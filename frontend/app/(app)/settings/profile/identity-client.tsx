"use client";

import { ProfileIdentity } from "@/components/screens/settings/account/ProfileIdentity";
import { useProfileIdentity } from "@/components/screens/settings/account/use-profile-identity";

export function ProfileIdentityClient() {
  return <ProfileIdentity {...useProfileIdentity()} />;
}
