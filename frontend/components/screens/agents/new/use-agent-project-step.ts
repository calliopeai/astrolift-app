"use client";

import { useWizardProjects } from "@/components/wizard/use-wizard-projects";

import { useQuery } from "@apollo/client/react";
import * as React from "react";

import { LIST_TEAMS } from "@/graphql/identity/identity.queries";
import type { AstroliftTeam } from "@/graphql/identity/identity.types";

interface TeamsResp {
  astroliftTeams: AstroliftTeam[];
}

/** The slice of the register-agent-repo wizard state the project step reads and writes. */
export interface AgentProjectFields {
  projectId: string;
}

/**
 * Teams and projects for the register-agent-repo project step, plus the
 * effects that write into the wizard state: auto-pick the first project and
 * report validity. The data half of AgentProjectStepView.
 */
export function useAgentProjectStep<S extends AgentProjectFields>(
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

  React.useEffect(() => {
    setValid(projectValid);
  }, [projectValid, setValid]);

  return { allTeams, allProjects };
}
