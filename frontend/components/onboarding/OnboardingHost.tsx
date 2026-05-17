"use client";

import { useQuery } from "@apollo/client/react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import * as React from "react";

import { GET_ONBOARDING_STATE } from "@/graphql/identity/identity.queries";

import { OnboardingWizard } from "./OnboardingWizard";
import { SpotlightTour } from "./SpotlightTour";

interface OnboardingStateResp {
  astroliftOrganizations: Array<{
    id: string;
    slug: string;
    name: string;
    onboardingCompletedAt: string | null;
  }>;
  astroliftTeams: Array<{ id: string }>;
}

const SPOTLIGHT_DONE_KEY = "astrolift-spotlight-complete-v1";

/**
 * Mounts the onboarding wizard + the spotlight tour around the
 * dashboard.  Behaviour:
 *
 *  * Auto-open the wizard when the active org has
 *    ``onboardingCompletedAt == null`` AND the user has zero teams.
 *    Both halves are required so an operator who joined an existing
 *    tenant doesn't get prompted for setup that's already done.
 *  * Manual re-run: query string ``?onboarding=1`` opens the wizard
 *    on demand from the user menu.
 *  * Spotlight tour fires once on completion; gated by
 *    ``localStorage[SPOTLIGHT_DONE_KEY]`` so it never re-shows on
 *    subsequent dashboard renders.
 *
 * Lives next to the dashboard mount so the GraphQL fetch piggybacks
 * on the dashboard's existing query budget instead of spinning up a
 * second SSR fan-out.
 */
export function OnboardingHost() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const manualTrigger = searchParams.get("onboarding") === "1";

  const { data } = useQuery<OnboardingStateResp>(GET_ONBOARDING_STATE, {
    fetchPolicy: "cache-and-network",
  });

  const [wizardOpen, setWizardOpen] = React.useState(false);
  const [tourOpen, setTourOpen] = React.useState(false);

  const org = React.useMemo(() => {
    const orgs = data?.astroliftOrganizations ?? [];
    return orgs[0] ?? null;
  }, [data]);

  const shouldAutoOpen = React.useMemo(() => {
    if (org == null) return false;
    if (org.onboardingCompletedAt != null) return false;
    const teamCount = data?.astroliftTeams?.length ?? 0;
    return teamCount === 0;
  }, [org, data]);

  // Open the wizard exactly once per page load — either from the
  // manual ?onboarding=1 trigger or from the auto-open heuristic.
  // The ref guards against re-opening if the query refetches after
  // a successful completion (which would otherwise see the stale
  // cache for a tick).
  const openedRef = React.useRef(false);
  React.useEffect(() => {
    if (openedRef.current) return;
    if (org == null) return;
    if (manualTrigger || shouldAutoOpen) {
      setWizardOpen(true);
      openedRef.current = true;
    }
  }, [manualTrigger, shouldAutoOpen, org]);

  // Strip the manual-trigger query param so a refresh doesn't keep
  // re-opening the wizard once the operator has dismissed it.
  React.useEffect(() => {
    if (!manualTrigger) return;
    if (wizardOpen) return;
    if (!openedRef.current) return;
    const params = new URLSearchParams(searchParams.toString());
    params.delete("onboarding");
    const query = params.toString();
    router.replace(query ? `${pathname}?${query}` : pathname);
  }, [manualTrigger, wizardOpen, pathname, router, searchParams]);

  function handleWizardClose() {
    setWizardOpen(false);
    // Only kick off the spotlight tour when it hasn't been
    // dismissed yet — repeat onboarding runs from the user menu
    // shouldn't re-arm the tour.
    if (typeof window === "undefined") return;
    try {
      if (window.localStorage.getItem(SPOTLIGHT_DONE_KEY) === "1") return;
    } catch {
      // localStorage may throw in restrictive contexts (Safari
      // private mode, sandboxed iframes); fall through to show the
      // tour rather than silently swallow it.
    }
    setTourOpen(true);
  }

  if (org == null) return null;

  return (
    <>
      <OnboardingWizard
        open={wizardOpen}
        onOpenChange={(next) => {
          if (!next) {
            handleWizardClose();
          } else {
            setWizardOpen(true);
          }
        }}
        organizationId={org.id}
        onComplete={() => {
          // onComplete fires AFTER the dialog has been closed by
          // the wizard itself; handleWizardClose already armed the
          // tour in that path, so this is a no-op for now. Kept as
          // a hook for future analytics.
        }}
      />
      <SpotlightTour
        open={tourOpen}
        onOpenChange={setTourOpen}
        onDismiss={() => {
          try {
            window.localStorage.setItem(SPOTLIGHT_DONE_KEY, "1");
          } catch {
            // ignore — see handleWizardClose
          }
          setTourOpen(false);
        }}
      />
    </>
  );
}
