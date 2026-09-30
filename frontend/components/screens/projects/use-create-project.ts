"use client";

import { useMutation } from "@apollo/client/react";
import { toast } from "sonner";

import { CREATE_PROJECT } from "@/graphql/identity/identity.mutations";
import { LIST_PROJECTS } from "@/graphql/identity/identity.queries";
import type { AstroliftProject, MutationResult } from "@/graphql/identity/identity.types";

import { slugify, isValidSlug } from "./project-team-slug";

export interface CreateProjectValues {
  teamId: string;
  name: string;
  slug: string;
  description: string;
}

/** The data half of CreateProjectSheet: the create mutation and its toasts. */
export function useCreateProject() {
  const [createProjectMutation, { loading }] = useMutation<{
    createProject: MutationResult<AstroliftProject>;
  }>(CREATE_PROJECT, {
    refetchQueries: [{ query: LIST_PROJECTS }],
    awaitRefetchQueries: true,
  });

  /** Resolves true when the project was created, so the sheet can close. */
  async function createProject(values: CreateProjectValues): Promise<boolean> {
    if (!values.teamId) return false;
    const finalSlug = values.slug.trim() || slugify(values.name);
    if (!values.name.trim() || !isValidSlug(finalSlug)) return false;
    const { data } = await createProjectMutation({
      variables: {
        input: {
          teamId: values.teamId,
          name: values.name.trim(),
          slug: finalSlug,
          description: values.description.trim() || null,
        },
      },
    });
    const result = data?.createProject;
    if (result?.ok) {
      toast.success(`Project ${finalSlug} created`);
      return true;
    }
    toast.error(result?.errors?.[0]?.message ?? "Create failed");
    return false;
  }

  return { creating: loading, createProject };
}
