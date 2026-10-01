"use client";

import { useMutation } from "@apollo/client/react";
import { toast } from "sonner";
import { useTranslations } from "next-intl";
import { refetchAfterMutation } from "@/lib/apollo/mutation-feedback";

import { slugify, isValidSlug } from "@/components/screens/projects/project-team-slug";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import { CREATE_TEAM } from "@/graphql/identity/identity.mutations";
import { LIST_TEAMS } from "@/graphql/identity/identity.queries";
import type { AstroliftTeam, MutationResult } from "@/graphql/identity/identity.types";

export interface CreateTeamValues {
  name: string;
  slug: string;
  description: string;
}

/** The data half of CreateTeamSheet: the active org, the create mutation and its toasts. */
export function useCreateTeam() {
  const t = useTranslations("teams");
  const { org } = useActiveOrg();

  const [createTeamMutation, { loading }] = useMutation<{
    createTeam: MutationResult<AstroliftTeam>;
  }>(CREATE_TEAM, {
    refetchQueries: (result) => (result.data?.createTeam.ok ? [{ query: LIST_TEAMS }] : []),
    onQueryUpdated: (query) => refetchAfterMutation(query, t("feedback.refreshWarning")),
    awaitRefetchQueries: true,
  });

  /** Resolves true when the team was created, so the sheet can close. */
  async function createTeam(values: CreateTeamValues): Promise<boolean> {
    if (!org) return false;
    const finalSlug = values.slug.trim() || slugify(values.name);
    if (!values.name.trim() || !isValidSlug(finalSlug)) return false;
    try {
      const { data } = await createTeamMutation({
        variables: {
          input: {
            organizationId: org.id,
            name: values.name.trim(),
            slug: finalSlug,
            description: values.description.trim() || null,
          },
        },
      });
      const result = data?.createTeam;
      if (result?.ok) {
        toast.success(t("feedback.created", { slug: finalSlug }));
        return true;
      }
      toast.error(result?.errors?.[0]?.message ?? t("feedback.createFailed"));
    } catch (error) {
      toast.error(
        error instanceof Error && error.message ? error.message : t("feedback.createFailed")
      );
    }
    return false;
  }

  return { hasOrg: org != null, creating: loading, createTeam };
}
