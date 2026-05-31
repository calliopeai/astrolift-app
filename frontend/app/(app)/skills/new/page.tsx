"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { gql } from "@apollo/client";
import { useMutation } from "@apollo/client/react";
import { Loader2Icon, PlusIcon } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PageShell } from "@/components/PageShell";
import { Separator } from "@/components/ui/separator";
import { Textarea } from "@/components/ui/textarea";

// ─── GraphQL ─────────────────────────────────────────────────────────────────

const CREATE_SKILL = gql`
  mutation CreateAgentSkill(
    $name: String!
    $slug: String!
    $description: String
    $instructions: String
  ) {
    createAgentSkill(
      name: $name
      slug: $slug
      description: $description
      instructions: $instructions
    ) {
      ok
      skillId
      errors {
        field
        messages
      }
    }
  }
`;

type CreateSkillResult = {
  createAgentSkill: {
    ok: boolean;
    skillId: string | null;
    errors: { field: string; messages: string[] }[];
  };
};

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function NewSkillPage() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [description, setDescription] = useState("");
  const [instructions, setInstructions] = useState("");

  const [createSkill, { loading }] = useMutation<CreateSkillResult>(CREATE_SKILL, {
    refetchQueries: ["ListAgentSkills"],
  });

  function autoSlug(value: string) {
    setSlug(
      value
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/^-|-$/g, "")
    );
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim() || !slug.trim()) {
      toast.error("Name and slug are required");
      return;
    }
    const { data } = await createSkill({
      variables: {
        name: name.trim(),
        slug: slug.trim(),
        description: description.trim() || undefined,
        instructions: instructions.trim() || undefined,
      },
    });
    if (data?.createAgentSkill?.ok && data.createAgentSkill.skillId) {
      toast.success("Skill created");
      router.push(`/skills/${data.createAgentSkill.skillId}`);
    } else {
      for (const err of data?.createAgentSkill?.errors ?? []) {
        toast.error(`${err.field}: ${err.messages.join(", ")}`);
      }
    }
  }

  return (
    <PageShell
      title="New skill"
      description="Define a skill — instructions, scripts, and tool bindings your agents draw on."
    >
      <form onSubmit={handleSubmit} className="flex max-w-2xl flex-col gap-6">
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="flex flex-col gap-1.5">
            <Label>Name</Label>
            <Input
              value={name}
              placeholder="e.g. Document Summariser"
              onChange={(e) => {
                setName(e.target.value);
                autoSlug(e.target.value);
              }}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label>Slug</Label>
            <Input
              value={slug}
              placeholder="document-summariser"
              onChange={(e) => setSlug(e.target.value)}
            />
          </div>
        </div>

        <div className="flex flex-col gap-1.5">
          <Label>Description</Label>
          <Input
            value={description}
            placeholder="Short description shown in search and picker"
            onChange={(e) => setDescription(e.target.value)}
          />
        </div>

        <Separator />

        <div className="flex flex-col gap-1.5">
          <Label>Instructions</Label>
          <Textarea
            value={instructions}
            placeholder="Prompt / instructions injected into the agent brief when this skill is active…"
            rows={8}
            className="font-mono text-sm"
            onChange={(e) => setInstructions(e.target.value)}
          />
        </div>

        <Button type="submit" disabled={loading} className="self-start">
          {loading ? (
            <Loader2Icon className="mr-2 h-4 w-4 animate-spin" />
          ) : (
            <PlusIcon className="mr-2 h-4 w-4" />
          )}
          Create skill
        </Button>
      </form>
    </PageShell>
  );
}
