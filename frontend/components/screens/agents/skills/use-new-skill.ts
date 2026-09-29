"use client";

import { useMutation } from "@apollo/client/react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";

import { CREATE_SKILL } from "@/graphql/agents/agents.mutations";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";

import { hasErrors, splitErrors } from "./catalog";
import {
  SKILL_FIELDS,
  type SkillErrors,
  type SkillFields,
  validateSkill,
} from "./use-skill-builder";

type MutError = { field: string; message: string; code: string };

type CreateSkillData = {
  createSkill: {
    ok: boolean;
    errors: MutError[];
    data: { id: string; name: string; slug: string; isActive: boolean } | null;
  };
};

export type NewSkillFields = SkillFields;

/**
 * Creates a skill in the active org and opens it. Refusals come back as
 * errors for the page to show beside their fields; the only toast is the
 * outcome. The data half of NewSkillScreen.
 */
export function useNewSkill() {
  const router = useRouter();
  // Reactive org id (#1022): the synchronous cookie read races the
  // post-render effect that sets it, leaving orgId "" on cold load.
  const { org } = useActiveOrg();
  const orgId = org?.id ?? "";

  const [createSkillMutation, { loading }] = useMutation<CreateSkillData>(CREATE_SKILL, {
    refetchQueries: ["ListSkills"],
  });

  async function createSkill(fields: NewSkillFields): Promise<SkillErrors> {
    const invalid = validateSkill(fields);
    if (hasErrors(invalid)) return invalid;
    try {
      const { data } = await createSkillMutation({
        variables: {
          orgId,
          input: {
            name: fields.name.trim(),
            slug: fields.slug.trim(),
            description: fields.description.trim(),
            content: fields.content.trim(),
            dependencies: null,
          },
        },
      });
      if (data?.createSkill?.ok && data.createSkill.data?.id) {
        toast.success("Skill created");
        router.push(`/agents/skills/${data.createSkill.data.id}`);
        return {};
      }
      const errors = splitErrors(data?.createSkill?.errors ?? [], SKILL_FIELDS);
      return hasErrors(errors) ? errors : { form: "The skill was not created." };
    } catch (err) {
      return { form: err instanceof Error ? err.message : String(err) };
    }
  }

  return { orgReady: !!orgId, loading, createSkill };
}

export type NewSkillState = ReturnType<typeof useNewSkill>;
