"use client";

import LoginButton from "@/components/LoginButton";
import LogoutButton from "@/components/LogoutButton";
import { Badge } from "@/components/ui/badge";

/**
 * Signed-in state with the sign-in or sign-out button. Pure (Storybook
 * first). Imported nowhere today: on the cut list for the migration's last
 * phase (spec 44 §9).
 */
export default function AuthStatus({
  loading,
  username,
}: {
  loading: boolean;
  /** Null when signed out. */
  username: string | null;
}) {
  if (loading) {
    return <Badge variant="secondary">Loading...</Badge>;
  }

  return (
    <div className="flex items-center gap-3">
      {username ? (
        <>
          <Badge variant="default">{username}</Badge>
          <LogoutButton />
        </>
      ) : (
        <>
          <Badge variant="outline">Not signed in</Badge>
          <LoginButton />
        </>
      )}
    </div>
  );
}
