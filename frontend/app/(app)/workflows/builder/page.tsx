"use client";

import { useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Loader2Icon, PlusIcon } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PageShell } from "@/components/PageShell";
import { Section } from "@/components/ui/section";
import { Separator } from "@/components/ui/separator";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import {
  useManifestImport,
  useWorkflowsEntitlement,
} from "@/graphql/workflows/tiered.hooks";

// ─── Patterns ─────────────────────────────────────────────────────────────────

type PatternKind =
  | "single"
  | "chained"
  | "fan_out"
  | "supervisor_worker"
  | "review_loop"
  | "advisor";

const PATTERNS: {
  value: PatternKind;
  label: string;
  description: string;
  diagram: string;
}[] = [
  {
    value: "single",
    label: "Single",
    description: "One agent stage. Simple task dispatch.",
    diagram: "[ Dispatch ]",
  },
  {
    value: "chained",
    label: "Chained",
    description: "Sequential stages — output of each feeds the next.",
    diagram: "[ A ] → [ B ] → [ C ]",
  },
  {
    value: "fan_out",
    label: "Fan-out",
    description: "Parallel stages that run simultaneously, then aggregate.",
    diagram: "[ Dispatch ] → ⌈ B ⌉ → [ Aggregate ]\n              ⌊ C ⌋",
  },
  {
    value: "supervisor_worker",
    label: "Supervisor / Worker",
    description: "Supervisor agent plans and routes work to specialised workers.",
    diagram: "[ Plan ] → [ Route ] → ⌈ Worker ⌉ → [ Aggregate ]",
  },
  {
    value: "review_loop",
    label: "Review loop",
    description: "Agent produces output, human reviews, agent revises — up to N rounds.",
    diagram: "[ Draft ] ⟷ [ Human gate ] → [ Deliver ]",
  },
  {
    value: "advisor",
    label: "Advisor",
    description: "Agent advises but humans retain final decision authority.",
    diagram: "[ Analyse ] → [ Recommend ] → [ Human gate ]",
  },
];

const PATTERN_VALUES = new Set<string>(PATTERNS.map((p) => p.value));

// ─── Helpers ──────────────────────────────────────────────────────────────────

function slugify(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 100);
}

// JSON string escaping is valid TOML basic-string escaping.
function tomlString(s: string): string {
  return JSON.stringify(s);
}

function buildMinimalToml(input: {
  slug: string;
  name: string;
  pattern: PatternKind;
  description: string;
}): string {
  return [
    "[workflow]",
    `slug = ${tomlString(input.slug)}`,
    `name = ${tomlString(input.name)}`,
    `pattern = ${tomlString(input.pattern)}`,
    `description = ${tomlString(input.description)}`,
    "",
  ].join("\n");
}

// ─── Sub-components ───────────────────────────────────────────────────────────

function PatternCard({
  pattern,
  selected,
  onSelect,
}: {
  pattern: (typeof PATTERNS)[0];
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      className={[
        "flex flex-col gap-2 rounded-lg border p-4 text-left transition-colors",
        selected
          ? "border-primary bg-primary/5"
          : "hover:bg-muted border-border",
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

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function NewWorkflowBuilderPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { org } = useActiveOrg();
  const { canCreate } = useWorkflowsEntitlement();
  const [importManifest] = useManifestImport();

  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [slugTouched, setSlugTouched] = useState(false);
  const [description, setDescription] = useState("");
  const [pattern, setPattern] = useState<PatternKind>(() => {
    const fromQuery = searchParams.get("pattern");
    return fromQuery && PATTERN_VALUES.has(fromQuery) ? (fromQuery as PatternKind) : "single";
  });
  const [creating, setCreating] = useState(false);

  function handleNameChange(value: string) {
    setName(value);
    if (!slugTouched) setSlug(slugify(value));
  }

  async function handleCreate() {
    const finalSlug = slug.trim() || slugify(name);
    if (!name.trim()) {
      toast.error("Name is required");
      return;
    }
    if (!finalSlug) {
      toast.error("Slug is required");
      return;
    }

    setCreating(true);
    try {
      const toml = buildMinimalToml({
        slug: finalSlug,
        name: name.trim(),
        pattern,
        description: description.trim(),
      });
      const { data } = await importManifest({
        variables: { toml, preview: false, orgId: org?.id ?? null },
      });
      const res = data?.importWorkflowManifest;
      if (res?.ok && res.createdSlug) {
        toast.success("Definition created", { description: res.createdSlug });
        router.push(`/workflows/${res.createdSlug}/builder`);
        return;
      }
      const errors = res?.errors ?? [];
      if (errors.length > 0) {
        for (const e of errors) toast.error(`${e.field}: ${e.messages.join(", ")}`);
      } else if (res?.manifest?.error) {
        toast.error(res.manifest.error);
      } else {
        toast.error("Failed to create the definition");
      }
    } finally {
      setCreating(false);
    }
  }

  return (
    <PageShell
      title="Workflow builder"
      description="Name your workflow, choose a pattern, then add stages in the builder."
      actions={
        <Button
          onClick={handleCreate}
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
