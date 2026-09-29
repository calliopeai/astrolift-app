"use client";

import { useQuery } from "@apollo/client/react";
import * as React from "react";

import { LIST_PROJECTS } from "@/graphql/identity/identity.queries";
import type { AstroliftProject } from "@/graphql/identity/identity.types";

import type { AppDeployStrategyFields } from "./AppDeployStrategyStep";

interface ProjectsResponse {
  astroliftProjects: AstroliftProject[];
}

export const CRON_FIELD = /^(\S+\s+){4}\S+$/;

/**
 * Wizard step 4 logic: resolves the org slug for the approver picker and
 * reports step validity up to the wizard.
 */
export function useAppDeployStrategyStep(
  state: AppDeployStrategyFields & { projectId: string },
  setValid: (valid: boolean) => void
) {
  // Resolve the org slug from the picked project — the approver
  // picker is org-scoped so the user list mirrors the project's org.
  const projectsQuery = useQuery<ProjectsResponse>(LIST_PROJECTS, {
    fetchPolicy: "cache-first",
  });
  const orgSlug = React.useMemo(() => {
    const projects = projectsQuery.data?.astroliftProjects ?? [];
    const picked = projects.find((p) => p.id === state.projectId);
    return picked?.organization?.slug ?? "";
  }, [projectsQuery.data, state.projectId]);

  const [approverPickerValid, setApproverPickerValid] = React.useState(true);

  // Validity:
  //   - skip: always valid (operator finishes config from the app page)
  //   - auto_on_push: deploy_branch required
  //   - cron: cron expression must be 5-field
  //   - manual: always valid
  //   - approval gate (when on): approver picker reports its own validity
  React.useEffect(() => {
    if (state.deployTiming === "skip") {
      setValid(true);
      return;
    }
    let ok = true;
    if (state.triggerMode === "auto_on_push") {
      ok = state.deployBranch.trim().length > 0;
    }
    if (state.triggerMode === "cron") {
      ok = CRON_FIELD.test(state.cronExpression.trim());
    }
    if (state.requiresApproval && !approverPickerValid) {
      ok = false;
    }
    setValid(ok);
  }, [
    state.deployTiming,
    state.triggerMode,
    state.deployBranch,
    state.cronExpression,
    state.requiresApproval,
    approverPickerValid,
    setValid,
  ]);

  return { orgSlug, setApproverPickerValid };
}
