"use client";

import { useQuery } from "@apollo/client/react";
import * as React from "react";

import { LIST_PROJECTS, LIST_TEAMS } from "@/graphql/identity/identity.queries";
import type { AstroliftProject, AstroliftTeam } from "@/graphql/identity/identity.types";

interface TeamsResp {
  astroliftTeams: AstroliftTeam[];
}

interface ProjectsResp {
  astroliftProjects: AstroliftProject[];
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
  const teams = useQuery<TeamsResp>(LIST_TEAMS, { fetchPolicy: "cache-and-network" });
  const projects = useQuery<ProjectsResp>(LIST_PROJECTS, { fetchPolicy: "cache-and-network" });

  const allProjects = React.useMemo(() => projects.data?.astroliftProjects ?? [], [projects.data]);
  const allTeams = teams.data?.astroliftTeams ?? [];

  // Auto-pick first project once data lands.
  React.useEffect(() => {
    if (!state.projectId && allProjects.length > 0) {
      setState((s) => (s.projectId ? s : { ...s, projectId: allProjects[0].id }));
    }
  }, [allProjects, state.projectId, setState]);

  React.useEffect(() => {
    setValid(state.projectId !== "");
  }, [state.projectId, setValid]);

  return { allTeams, allProjects };
}
