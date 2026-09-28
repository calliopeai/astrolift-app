"use client";

import { useMutation } from "@apollo/client/react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";

import { CREATE_SKILL } from "@/graphql/agents/agents.mutations";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";

type MutError = { field: string; message: string; code: string };

type CreateSkillData = {
  createSkill: {
    ok: boolean;
    errors: MutError[];
    data: { id: string; name: string; slug: string; isActive: boolean } | null;
  };
};

export type NewSkillFields = {
  name: string;
  slug: string;
  description: string;
  content: string;
};

/** Creates a skill in the active org and opens it. The data half of NewSkillScreen. */
export function useNewSkill() {
  const router = useRouter();
  // Reactive org id (#1022): the synchronous cookie read races the
  // post-render effect that sets it, leaving orgId "" on cold load.
  const { org } = useActiveOrg();
  const orgId = org?.id ?? "";

  const [createSkillMutation, { loading }] = useMutation<CreateSkillData>(CREATE_SKILL, {
    refetchQueries: ["ListSkills"],
  });

  async function createSkill(fields: NewSkillFields) {
    if (!fields.name.trim() || !fields.slug.trim()) {
      toast.error("Name and slug are required");
      return;
    }
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
    } else {
      for (const err of data?.createSkill?.errors ?? []) {
        toast.error(`${err.field}: ${err.message}`);
      }
    }
  }

  return { orgReady: !!orgId, loading, createSkill };
}

export type NewSkillState = ReturnType<typeof useNewSkill>;
