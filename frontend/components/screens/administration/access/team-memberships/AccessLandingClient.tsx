"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import { PEOPLE_HREF, TEAMS_HREF } from "../access-nav";
import { useTeamMembershipScope } from "./use-team-membership-scope";
import { useTeamAccessNavigation } from "./use-team-access-navigation";

export function AccessLandingClient() {
  const scope = useTeamMembershipScope();
  return <AccessLanding key={scope.key} ready={scope.ready} />;
}
function AccessLanding({ ready }: { ready: boolean }) {
  const router = useRouter(),
    nav = useTeamAccessNavigation(ready);
  const href = nav.navigation?.canViewPeople
    ? PEOPLE_HREF
    : nav.navigation?.canViewTeams
      ? TEAMS_HREF
      : null;
  useEffect(() => {
    if (ready && href) router.replace(href);
  }, [href, ready, router]);
  return (
    <AccessLandingPanel
      loading={!ready || nav.loading || Boolean(href)}
      error={nav.error?.message ?? null}
      onRetry={() => {
        void nav.refetch().catch(() => {});
      }}
    />
  );
}

export function AccessLandingPanel({
  loading,
  error,
  onRetry,
}: {
  loading: boolean;
  error: string | null;
  onRetry: () => void;
}) {
  const t = useTranslations("teams.memberships");
  return (
    <div className="p-6" aria-busy={loading}>
      {loading ? (
        <p>{t("loading")}</p>
      ) : (
        <div role="alert">
          <p>{error ?? t("unavailable")}</p>
          <Button onClick={onRetry}>{t("retry")}</Button>
        </div>
      )}
    </div>
  );
}
