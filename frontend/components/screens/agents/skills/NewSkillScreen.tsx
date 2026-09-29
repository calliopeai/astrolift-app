"use client";

import { AlertTriangleIcon, ArrowLeftIcon, ArrowRightIcon, Loader2Icon } from "lucide-react";
import Link from "next/link";
import * as React from "react";

import { ShellHeader } from "@/components/shell/ShellHeader";
import { Button } from "@/components/ui/button";
import { Field, FieldError, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";

import { agentsCrumbs } from "./catalog";
import { FlowSteps } from "./FlowSteps";
import type { SkillErrors } from "./use-skill-builder";
import type { NewSkillState } from "./use-new-skill";

export type NewSkillScreenProps = NewSkillState & {
  /** Start on a step; stories use it. */
  initialStep?: 1 | 2;
  /** Errors to open with; stories use it. */
  initialErrors?: SkillErrors;
};

const STEPS = [{ label: "Skill" }, { label: "Instructions" }];

const slugify = (value: string) =>
  value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");

/**
 * Agents › Skills › New skill: four fields, so a page in two steps (spec 44
 * §5.4). Errors stand beside their fields, and a refused name or slug sends
 * the page back to the first step; the outcome is the hook's toast. Holds
 * the field values; useNewSkill creates the skill and opens it.
 */
export function NewSkillScreen({
  orgReady,
  loading,
  createSkill,
  initialStep = 1,
  initialErrors = {},
}: NewSkillScreenProps) {
  const [step, setStep] = React.useState<1 | 2>(initialStep);
  const [name, setName] = React.useState("");
  const [slug, setSlug] = React.useState("");
  const [slugTouched, setSlugTouched] = React.useState(false);
  const [description, setDescription] = React.useState("");
  const [content, setContent] = React.useState("");
  const [errors, setErrors] = React.useState<SkillErrors>(initialErrors);

  const clear = (field: keyof SkillErrors) =>
    setErrors((e) => ({ ...e, [field]: undefined, form: undefined }));

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (step === 1) {
      const found: SkillErrors = {};
      if (!name.trim()) found.name = "Give the skill a name.";
      if (!slug.trim()) found.slug = "A slug is required.";
      setErrors(found);
      if (!found.name && !found.slug) setStep(2);
      return;
    }
    const found = await createSkill({ name, slug, description, content });
    setErrors(found);
    if (found.name || found.slug || found.description) setStep(1);
  }

  return (
    <div className="flex min-w-0 flex-1 flex-col gap-6">
      <ShellHeader
        crumbs={agentsCrumbs("skills", { label: "New skill" })}
        title="New skill"
        context={<FlowSteps steps={STEPS} current={step} />}
      />

      <p className="text-muted-foreground max-w-2xl text-sm">
        Define a skill: instructions and tool bindings your agents draw on at dispatch time.
      </p>

      <form
        onSubmit={submit}
        noValidate
        className="bg-card flex max-w-3xl min-w-0 flex-col gap-4 rounded-md border p-6"
      >
        {errors.form && (
          <div
            role="alert"
            className="border-destructive/40 bg-destructive/5 text-destructive flex min-w-0 items-start gap-2 rounded-md border p-3 text-sm"
          >
            <AlertTriangleIcon className="mt-0.5 size-4 shrink-0" aria-hidden />
            <span className="min-w-0 [overflow-wrap:anywhere]">{errors.form}</span>
          </div>
        )}

        {step === 1 ? (
          <>
            <div className="grid min-w-0 gap-4 sm:grid-cols-2">
              <Field data-invalid={Boolean(errors.name) || undefined} className="min-w-0">
                <FieldLabel htmlFor="new-skill-name">Name</FieldLabel>
                <Input
                  id="new-skill-name"
                  value={name}
                  placeholder="e.g. Document Summariser"
                  aria-invalid={Boolean(errors.name) || undefined}
                  onChange={(e) => {
                    setName(e.target.value);
                    clear("name");
                    if (!slugTouched) setSlug(slugify(e.target.value));
                  }}
                />
                <FieldError className="[overflow-wrap:anywhere]">{errors.name}</FieldError>
              </Field>
              <Field data-invalid={Boolean(errors.slug) || undefined} className="min-w-0">
                <FieldLabel htmlFor="new-skill-slug">Slug</FieldLabel>
                <Input
                  id="new-skill-slug"
                  value={slug}
                  placeholder="document-summariser"
                  className="font-mono"
                  spellCheck={false}
                  aria-invalid={Boolean(errors.slug) || undefined}
                  onChange={(e) => {
                    setSlug(e.target.value);
                    setSlugTouched(true);
                    clear("slug");
                  }}
                />
                <FieldError className="[overflow-wrap:anywhere]">{errors.slug}</FieldError>
              </Field>
            </div>
            <Field data-invalid={Boolean(errors.description) || undefined} className="min-w-0">
              <FieldLabel htmlFor="new-skill-description">Description</FieldLabel>
              <Input
                id="new-skill-description"
                value={description}
                placeholder="Short description shown in search and picker"
                aria-invalid={Boolean(errors.description) || undefined}
                onChange={(e) => {
                  setDescription(e.target.value);
                  clear("description");
                }}
              />
              <FieldError className="[overflow-wrap:anywhere]">{errors.description}</FieldError>
            </Field>
          </>
        ) : (
          <Field data-invalid={Boolean(errors.content) || undefined} className="min-w-0">
            <FieldLabel htmlFor="new-skill-content">Instructions / content</FieldLabel>
            <Textarea
              id="new-skill-content"
              value={content}
              placeholder="System prompt / instructions injected into the agent brief when this skill is active"
              rows={10}
              className="max-h-96 font-mono text-sm"
              aria-invalid={Boolean(errors.content) || undefined}
              onChange={(e) => {
                setContent(e.target.value);
                clear("content");
              }}
            />
            <FieldError className="[overflow-wrap:anywhere]">{errors.content}</FieldError>
          </Field>
        )}

        <div className="flex min-w-0 flex-wrap items-center justify-end gap-2 border-t pt-4">
          <Button type="button" variant="ghost" asChild className="mr-auto">
            <Link href="/agents/skills">Cancel</Link>
          </Button>
          {step === 2 && (
            <Button type="button" variant="outline" onClick={() => setStep(1)}>
              <ArrowLeftIcon className="size-4" />
              Back
            </Button>
          )}
          {step === 1 ? (
            <Button type="submit">
              Continue
              <ArrowRightIcon className="size-4" />
            </Button>
          ) : (
            <Button type="submit" disabled={loading || !orgReady}>
              {loading && <Loader2Icon className="size-4 animate-spin" />}
              Create skill
            </Button>
          )}
        </div>
      </form>
    </div>
  );
}
