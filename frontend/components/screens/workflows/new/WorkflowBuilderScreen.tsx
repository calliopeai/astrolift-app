"use client";

import { useState } from "react";
import { Loader2Icon, PlusIcon } from "lucide-react";

import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Section } from "@/components/ui/section";
import { Separator } from "@/components/ui/separator";

import type { useWorkflowBuilder } from "./use-workflow-builder";
import { PATTERNS, slugify, type PatternKind, type PatternOption } from "./workflow-patterns";

export type WorkflowBuilderScreenProps = ReturnType<typeof useWorkflowBuilder> & {
  /** Pattern preselected from `?pattern=`; read once on mount. */
  initialPattern: PatternKind;
};

export function PatternCard({
  pattern,
  selected,
  onSelect,
}: {
  pattern: PatternOption;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      className={[
        "flex flex-col gap-2 rounded-lg border p-4 text-left transition-colors",
        selected ? "border-primary bg-primary/5" : "hover:bg-muted border-border",
      ].join(" ")}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="font-medium">{pattern.label}</span>
        {selected && (
          <Badge variant="default" className="text-xs">
            Selected
          </Badge>
        )}
      </div>
      <p className="text-muted-foreground text-xs">{pattern.description}</p>
      <pre className="text-muted-foreground mt-1 overflow-x-auto text-xs leading-relaxed">
        {pattern.diagram}
      </pre>
    </button>
  );
}

/** Name a new workflow definition, choose its pattern, then open the stage builder. */
export function WorkflowBuilderScreen({
  canCreate,
  creating,
  onCreate,
  initialPattern,
}: WorkflowBuilderScreenProps) {
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [slugTouched, setSlugTouched] = useState(false);
  const [description, setDescription] = useState("");
  const [pattern, setPattern] = useState<PatternKind>(initialPattern);

  function handleNameChange(value: string) {
    setName(value);
    if (!slugTouched) setSlug(slugify(value));
  }

  return (
    <PageShell
      title="Workflow builder"
      description="Name your workflow, choose a pattern, then add stages in the builder."
      actions={
        <Button
          onClick={() => onCreate({ name, slug, description, pattern })}
          disabled={creating || !canCreate}
          title={canCreate ? undefined : "You don't have create access"}
        >
          {creating ? (
            <Loader2Icon className="mr-1 h-4 w-4 animate-spin" />
          ) : (
            <PlusIcon className="mr-1 h-4 w-4" />
          )}
          Create & open builder
        </Button>
      }
    >
      <div className="flex max-w-5xl flex-col gap-8">
        {/* Metadata */}
        <Section title="1. Workflow metadata">
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="flex flex-col gap-1.5">
              <Label>Name</Label>
              <Input
                value={name}
                placeholder="e.g. PR Review Loop"
                onChange={(e) => handleNameChange(e.target.value)}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label>Slug</Label>
              <Input
                value={slug}
                placeholder="e.g. pr-review-loop"
                onChange={(e) => {
                  setSlugTouched(true);
                  setSlug(slugify(e.target.value));
                }}
              />
            </div>
            <div className="flex flex-col gap-1.5 sm:col-span-2">
              <Label>Description</Label>
              <Input
                value={description}
                placeholder="What does this workflow do?"
                onChange={(e) => setDescription(e.target.value)}
              />
            </div>
          </div>
        </Section>

        <Separator />

        {/* Pattern picker */}
        <Section
          title="2. Choose a pattern"
          description="Stages are added in the builder after the definition is created."
        >
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {PATTERNS.map((p) => (
              <PatternCard
                key={p.value}
                pattern={p}
                selected={pattern === p.value}
                onSelect={() => setPattern(p.value)}
              />
            ))}
          </div>
        </Section>
      </div>
    </PageShell>
  );
}
