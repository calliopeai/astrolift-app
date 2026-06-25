"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useMutation } from "@apollo/client/react";
import { Loader2Icon, PlusIcon } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PageShell } from "@/components/PageShell";
import { Separator } from "@/components/ui/separator";
import { Textarea } from "@/components/ui/textarea";
import { CREATE_SKILL } from "@/graphql/agents/agents.mutations";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";

// ─── Types ────────────────────────────────────────────────────────────────────

type MutError = { field: string; message: string; code: string };

type CreateSkillData = {
  createSkill: {
    ok: boolean;
    errors: MutError[];
    data: { id: string; name: string; slug: string; isActive: boolean } | null;
  };
};

export default function NewAgentSkillPage() {
  const router = useRouter();
  // Reactive org id (#1022): the synchronous cookie read races the
  // post-render effect that sets it, leaving orgId "" on cold load.
  const { org } = useActiveOrg();
  const orgId = org?.id ?? "";

  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [description, setDescription] = useState("");
  const [content, setContent] = useState("");

  const [createSkill, { loading }] = useMutation<CreateSkillData>(CREATE_SKILL, {
    refetchQueries: ["ListSkills"],
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
        orgId,
        input: {
          name: name.trim(),
          slug: slug.trim(),
          description: description.trim(),
          content: content.trim(),
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

  return (
    <PageShell
      title="New skill"
      description="Define a skill — instructions and tool bindings your agents draw on at dispatch time."
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
              className="font-mono"
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
          <Label>Instructions / content</Label>
          <Textarea
            value={content}
            placeholder="System prompt / instructions injected into the agent brief when this skill is active"
            rows={8}
            className="font-mono text-sm"
            onChange={(e) => setContent(e.target.value)}
          />
        </div>

        <Button type="submit" disabled={loading || !orgId} className="self-start">
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
