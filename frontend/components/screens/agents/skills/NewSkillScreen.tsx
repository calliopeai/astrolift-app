"use client";

import { Loader2Icon, PlusIcon } from "lucide-react";
import { useState } from "react";

import { PageShell } from "@/components/PageShell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { Textarea } from "@/components/ui/textarea";

import type { NewSkillState } from "./use-new-skill";

export type NewSkillScreenProps = NewSkillState;

/** The new-skill form. Holds the field values; useNewSkill creates the skill and navigates. */
export function NewSkillScreen({ orgReady, loading, createSkill }: NewSkillScreenProps) {
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [description, setDescription] = useState("");
  const [content, setContent] = useState("");

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
    await createSkill({ name, slug, description, content });
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

        <Button type="submit" disabled={loading || !orgReady} className="self-start">
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
