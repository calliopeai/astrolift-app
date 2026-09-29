"use client";

import {
  AlertTriangleIcon,
  CodeIcon,
  FileTextIcon,
  Loader2Icon,
  MoreHorizontalIcon,
  SparklesIcon,
  TrashIcon,
  WrenchIcon,
} from "lucide-react";
import { useEffect, useState } from "react";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { ListSummary } from "@/components/list/ListSummary";
import { Panel, PanelGrid } from "@/components/panel/Panel";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Field, FieldError, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";

import { ADAPTER_LABEL } from "./tool-adapters";
import { SkillFrame, skillTabHref } from "./SkillFrame";
import type { SkillBuilderState, SkillErrors } from "./use-skill-builder";

export type SkillBuilderScreenProps = SkillBuilderState & {
  /** Errors to open with; stories use it. */
  initialErrors?: SkillErrors;
};

const FORM_ID = "skill-builder-form";

/**
 * A skill's Builder tab (spec 44 §5.2): its details and instructions on
 * Panels, saved from the title row, and a summary of its tools that links to
 * the Tools tab, where they are listed and registered (Leo's list rule 3).
 * Errors stand beside their fields. Holds the form values; useSkillBuilder
 * owns every query and mutation.
 */
export function SkillBuilderScreen({
  id,
  skill,
  skillLoading,
  errorMessage,
  onRetry,
  tools,
  toolsLoading,
  toolsError,
  onToolsRetry,
  saving,
  deleting,
  aiAssisting,
  saveSkill,
  deleteSkill,
  aiAssist,
  initialErrors = {},
}: SkillBuilderScreenProps) {
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [description, setDescription] = useState("");
  const [content, setContent] = useState("");
  const [dirty, setDirty] = useState(false);
  const [errors, setErrors] = useState<SkillErrors>(initialErrors);
  const [confirmDelete, setConfirmDelete] = useState(false);

  // Seed the form when the skill loads, and again after a save.
  useEffect(() => {
    if (skill && !dirty) {
      /* eslint-disable react-hooks/set-state-in-effect -- the fields copy the loaded skill until the person edits them */
      setName(skill.name);
      setSlug(skill.slug);
      setDescription(skill.description);
      setContent(skill.content);
      /* eslint-enable react-hooks/set-state-in-effect */
    }
  }, [skill, dirty]);

  function edit<T>(set: (v: T) => void, field: keyof SkillErrors) {
    return (v: T) => {
      set(v);
      setDirty(true);
      setErrors((e) => ({ ...e, [field]: undefined, form: undefined }));
    };
  }

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    const found = await saveSkill({ name, slug, description, content });
    setErrors(found);
    if (Object.values(found).every((v) => !v)) setDirty(false);
  }

  async function handleAiAssist() {
    const generated = await aiAssist({ name, description, content });
    if (generated) {
      setContent(generated);
      setDirty(true);
    }
  }

  const primaryAction = (
    <>
      {dirty && <span className="text-muted-foreground text-xs">Unsaved changes</span>}
      <Button type="submit" form={FORM_ID} size="sm" disabled={saving || !dirty}>
        {saving && <Loader2Icon className="size-4 animate-spin" />}
        Save skill
      </Button>
    </>
  );

  const menu = (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button size="icon" variant="ghost" className="size-8" aria-label="More actions">
          <MoreHorizontalIcon className="size-4" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-48">
        <DropdownMenuItem
          variant="destructive"
          disabled={deleting}
          onSelect={() => setConfirmDelete(true)}
        >
          <TrashIcon className="size-4" />
          Delete skill
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );

  return (
    <SkillFrame
      id={id}
      active="builder"
      skill={skill}
      loading={skillLoading}
      error={errorMessage}
      onRetry={onRetry}
      primaryAction={primaryAction}
      menu={menu}
    >
      <form id={FORM_ID} onSubmit={handleSave} noValidate className="flex min-w-0 flex-col gap-4">
        {errors.form && (
          <div
            role="alert"
            className="border-destructive/40 bg-destructive/5 text-destructive flex min-w-0 items-start gap-2 rounded-md border p-3 text-sm"
          >
            <AlertTriangleIcon className="mt-0.5 size-4 shrink-0" aria-hidden />
            <span className="min-w-0 [overflow-wrap:anywhere]">{errors.form}</span>
          </div>
        )}
        <PanelGrid>
          <Panel title="Details" icon={<FileTextIcon className="size-4" />} span={8}>
            <div className="flex min-w-0 flex-col gap-4">
              <div className="grid min-w-0 gap-4 sm:grid-cols-2">
                <Field data-invalid={Boolean(errors.name) || undefined} className="min-w-0">
                  <FieldLabel htmlFor="skill-name">Name</FieldLabel>
                  <Input
                    id="skill-name"
                    value={name}
                    aria-invalid={Boolean(errors.name) || undefined}
                    onChange={(e) => edit(setName, "name")(e.target.value)}
                  />
                  <FieldError className="[overflow-wrap:anywhere]">{errors.name}</FieldError>
                </Field>
                <Field data-invalid={Boolean(errors.slug) || undefined} className="min-w-0">
                  <FieldLabel htmlFor="skill-slug">Slug</FieldLabel>
                  <Input
                    id="skill-slug"
                    value={slug}
                    className="font-mono"
                    spellCheck={false}
                    aria-invalid={Boolean(errors.slug) || undefined}
                    onChange={(e) => edit(setSlug, "slug")(e.target.value)}
                  />
                  <FieldError className="[overflow-wrap:anywhere]">{errors.slug}</FieldError>
                </Field>
              </div>
              <Field data-invalid={Boolean(errors.description) || undefined} className="min-w-0">
                <FieldLabel htmlFor="skill-description">Description</FieldLabel>
                <Input
                  id="skill-description"
                  value={description}
                  placeholder="Short description shown in search and picker"
                  aria-invalid={Boolean(errors.description) || undefined}
                  onChange={(e) => edit(setDescription, "description")(e.target.value)}
                />
                <FieldError className="[overflow-wrap:anywhere]">{errors.description}</FieldError>
              </Field>
            </div>
          </Panel>

          <ListSummary
            title="Tools"
            icon={<WrenchIcon className="size-4" />}
            span={4}
            count={toolsLoading || toolsError ? null : tools.length}
            rows={tools}
            keyOf={(t) => t.id}
            renderRow={(t) => (
              <span className="flex min-w-0 items-center justify-between gap-2">
                <span className="min-w-0 truncate font-mono text-xs" title={t.name}>
                  {t.name}
                </span>
                <Badge variant="secondary" className="shrink-0 text-xs">
                  {ADAPTER_LABEL[t.adapter] ?? t.adapter}
                </Badge>
              </span>
            )}
            rowHref={(t) => `/agents/tools/${t.id}`}
            viewAllHref={skillTabHref(id, "tools")}
            loading={toolsLoading}
            error={toolsError}
            onRetry={onToolsRetry}
            empty={
              tools.length === 0
                ? {
                    icon: <CodeIcon className="size-5" />,
                    title: "No tool definitions",
                    description:
                      "Register tool definitions to give this skill executable capabilities.",
                    actionHref: `${skillTabHref(id, "tools")}/new`,
                    actionLabel: "Register first tool",
                  }
                : null
            }
          />

          <Panel
            title="Instructions"
            icon={<SparklesIcon className="size-4" />}
            description="Injected into the agent brief when this skill is active."
            actions={
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={handleAiAssist}
                disabled={aiAssisting}
              >
                {aiAssisting ? (
                  <Loader2Icon className="size-3.5 animate-spin" />
                ) : (
                  <SparklesIcon className="size-3.5" />
                )}
                AI assist
              </Button>
            }
          >
            <Field data-invalid={Boolean(errors.content) || undefined} className="min-w-0">
              <FieldLabel htmlFor="skill-content" className="sr-only">
                Instructions / content
              </FieldLabel>
              <Textarea
                id="skill-content"
                value={content}
                placeholder="System prompt / instructions injected into the agent brief when this skill is active"
                rows={12}
                className="max-h-96 font-mono text-sm"
                aria-invalid={Boolean(errors.content) || undefined}
                onChange={(e) => edit(setContent, "content")(e.target.value)}
              />
              <FieldError className="[overflow-wrap:anywhere]">{errors.content}</FieldError>
            </Field>
          </Panel>
        </PanelGrid>
      </form>

      {skill && (
        <ConfirmDialog
          open={confirmDelete}
          onOpenChange={setConfirmDelete}
          title={`Delete skill "${skill.name}"?`}
          description="This cannot be undone. Agents that reference this skill lose it on their next run, and its tool definitions go with it."
          confirmLabel="Delete skill"
          destructive
          onConfirm={deleteSkill}
        />
      )}
    </SkillFrame>
  );
}
