"use client";

import { useWizardProjects } from "@/components/wizard/use-wizard-projects";

import { useQuery } from "@apollo/client/react";
import * as React from "react";

import { LIST_TEAMS } from "@/graphql/identity/identity.queries";
import type { AstroliftTeam } from "@/graphql/identity/identity.types";
import { friendlyNameFromSlug, generateFriendlySlug } from "@/lib/friendly-name";

interface TeamsResp {
  astroliftTeams: AstroliftTeam[];
}

/** The slice of the register-app wizard state the details step reads and writes. */
export interface AppDetailsFields {
  name: string;
  slug: string;
  slugTouched: boolean;
  description: string;
  projectId: string;
}

const SLUG_RE = /^[a-z0-9-]{1,40}$/;

/**
 * Teams and projects for the register-app details step, plus the effects
 * that write into the wizard state: auto-pick the first project, pre-fill
 * a friendly name, and report validity. The data half of AppDetailsStepView.
 */
export function useAppDetailsStep<S extends AppDetailsFields>(
  state: S,
  setState: React.Dispatch<React.SetStateAction<S>>,
  setValid: (valid: boolean) => void
) {
  const {
    allProjects,
    orgId,
    loading: projectsLoading,
    error: projectsError,
  } = useWizardProjects();
  const teams = useQuery<TeamsResp>(LIST_TEAMS, { skip: !orgId, fetchPolicy: "cache-and-network" });
  const allTeams = (teams.data?.astroliftTeams ?? []).filter(
    (team) => team.organization.id === orgId && !team.deletedAt
  );
  const projectValid =
    !projectsLoading &&
    !projectsError &&
    allProjects.some((project) => project.id === state.projectId);

  // Auto-pick first project once data lands.
  React.useEffect(() => {
    if (!state.projectId && allProjects.length > 0) {
      setState((s) => (s.projectId ? s : { ...s, projectId: allProjects[0].id }));
    }
  }, [allProjects, state.projectId, setState]);

  // Pre-fill a friendly name so registration never starts nameless
  // (`exciting-talkative-platypus`). Suggestion only — the operator can type
  // over it, and we never clobber a name they've already entered. Generated in
  // a mount effect (not initial state) to avoid an SSR/client hydration
  // mismatch on the random value.
  const prefilledName = React.useRef(false);
  React.useEffect(() => {
    if (prefilledName.current) return;
    prefilledName.current = true;
    setState((s) => {
      if (s.name.trim() || s.slug.trim() || s.slugTouched) return s;
      const slug = generateFriendlySlug();
      return { ...s, name: friendlyNameFromSlug(slug), slug };
    });
  }, [setState]);

  const slugValid = SLUG_RE.test(state.slug);
  const nameValid = state.name.trim().length > 0;

  React.useEffect(() => {
    setValid(nameValid && slugValid && projectValid);
  }, [nameValid, slugValid, projectValid, setValid]);

  return { allTeams, allProjects, slugValid, projectsLoading };
}
