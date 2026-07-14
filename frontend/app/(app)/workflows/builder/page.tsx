"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import {
  ArrowDownIcon,
  ArrowUpIcon,
  GripVerticalIcon,
  Loader2Icon,
  PlusIcon,
  SaveIcon,
  TrashIcon,
  WorkflowIcon,
} from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { EmptyState } from "@/components/EmptyState";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PageShell } from "@/components/PageShell";
import { Section } from "@/components/ui/section";
import { Separator } from "@/components/ui/separator";
import { Textarea } from "@/components/ui/textarea";

// ─── Constants ────────────────────────────────────────────────────────────────

type PatternKind =
  | "single"
  | "chained"
  | "fan_out"
  | "supervisor_worker"
  | "review_loop"
  | "advisor";

type StageKind = "agent_dispatch" | "human_gate" | "checkpoint" | "aggregation";
type OnFailurePolicy = "fail_workflow" | "skip" | "retry";

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

const STAGE_KINDS: { value: StageKind; label: string }[] = [
  { value: "agent_dispatch", label: "Agent dispatch" },
  { value: "human_gate", label: "Human gate" },
  { value: "checkpoint", label: "Checkpoint" },
  { value: "aggregation", label: "Aggregation" },
];

const ON_FAILURE_POLICIES: { value: OnFailurePolicy; label: string }[] = [
  { value: "fail_workflow", label: "Fail workflow" },
  { value: "skip", label: "Skip stage" },
  { value: "retry", label: "Retry" },
];

// ─── Types ────────────────────────────────────────────────────────────────────

type Stage = {
  id: string;
  kind: StageKind;
  agentWorkload: string;
  onFailure: OnFailurePolicy;
  timeoutSeconds: number;
};

// ─── Small helpers ────────────────────────────────────────────────────────────

function uid(): string {
  return Math.random().toString(36).slice(2, 9);
}

function makeStage(): Stage {
  return {
    id: uid(),
    kind: "agent_dispatch",
    agentWorkload: "",
    onFailure: "fail_workflow",
    timeoutSeconds: 300,
  };
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

function StageCard({
  stage,
  index,
  total,
  onChange,
  onRemove,
  onMoveUp,
  onMoveDown,
}: {
  stage: Stage;
  index: number;
  total: number;
  onChange: (patch: Partial<Stage>) => void;
  onRemove: () => void;
  onMoveUp: () => void;
  onMoveDown: () => void;
}) {
  return (
    <Card>
      <CardContent className="flex gap-3">
        <div className="text-muted-foreground flex flex-col items-center gap-1 pt-1">
          <GripVerticalIcon className="h-4 w-4" />
          <span className="text-xs font-medium">{index + 1}</span>
        </div>

        <div className="flex flex-1 flex-col gap-3">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {/* Kind */}
            <div className="flex flex-col gap-1">
              <Label className="text-xs">Stage kind</Label>
              <select
                value={stage.kind}
                onChange={(e) => onChange({ kind: e.target.value as StageKind })}
                className="border-input bg-background rounded-md border px-3 py-1.5 text-sm"
              >
                {STAGE_KINDS.map((k) => (
                  <option key={k.value} value={k.value}>
                    {k.label}
                  </option>
                ))}
              </select>
            </div>

            {/* Agent workload */}
            <div className="flex flex-col gap-1">
              <Label className="text-xs">Agent workload</Label>
              <Input
                value={stage.agentWorkload}
                placeholder="e.g. review-agent"
                className="h-8 text-sm"
                onChange={(e) => onChange({ agentWorkload: e.target.value })}
              />
            </div>

            {/* On failure */}
            <div className="flex flex-col gap-1">
              <Label className="text-xs">On failure</Label>
              <select
                value={stage.onFailure}
                onChange={(e) =>
                  onChange({ onFailure: e.target.value as OnFailurePolicy })
                }
                className="border-input bg-background rounded-md border px-3 py-1.5 text-sm"
              >
                {ON_FAILURE_POLICIES.map((p) => (
                  <option key={p.value} value={p.value}>
                    {p.label}
                  </option>
                ))}
              </select>
            </div>

            {/* Timeout */}
            <div className="flex flex-col gap-1">
              <Label className="text-xs">Timeout (seconds)</Label>
              <Input
                type="number"
                min={1}
                max={86400}
                value={stage.timeoutSeconds}
                className="h-8 text-sm"
                onChange={(e) =>
                  onChange({ timeoutSeconds: parseInt(e.target.value, 10) || 300 })
                }
              />
            </div>
          </div>
        </div>

        {/* Actions */}
        <div className="flex flex-col gap-1">
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="h-7 w-7"
            disabled={index === 0}
            onClick={onMoveUp}
            title="Move up"
          >
            <ArrowUpIcon className="h-3.5 w-3.5" />
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="h-7 w-7"
            disabled={index === total - 1}
            onClick={onMoveDown}
            title="Move down"
          >
            <ArrowDownIcon className="h-3.5 w-3.5" />
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="text-destructive hover:text-destructive hover:bg-destructive/10 h-7 w-7"
            onClick={onRemove}
            title="Remove stage"
          >
            <TrashIcon className="h-3.5 w-3.5" />
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function WorkflowBuilderPage() {
  const router = useRouter();

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [pattern, setPattern] = useState<PatternKind>("chained");
  const [stages, setStages] = useState<Stage[]>([]);
  const [saving, setSaving] = useState(false);

  function addStage() {
    setStages((prev) => [...prev, makeStage()]);
  }

  function removeStage(id: string) {
    setStages((prev) => prev.filter((s) => s.id !== id));
  }

  function patchStage(id: string, patch: Partial<Stage>) {
    setStages((prev) =>
      prev.map((s) => (s.id === id ? { ...s, ...patch } : s))
    );
  }

  function moveStage(index: number, direction: -1 | 1) {
    const next = [...stages];
    const target = index + direction;
    if (target < 0 || target >= next.length) return;
    [next[index], next[target]] = [next[target], next[index]];
    setStages(next);
  }

  async function handleSave() {
    if (!name.trim()) {
      toast.error("Name is required");
      return;
    }
    if (stages.length === 0) {
      toast.error("Add at least one stage");
      return;
    }

    setSaving(true);
    try {
      // The GQL mutation for agent-dispatch WorkflowDefinitions (#54) is
      // pending the backend ticket. Save is wired to the existing
      // createWorkflowDefinition mutation shape for now; a follow-up
      // adds the pattern + stage fields once the schema lands.
      toast.info("Workflow builder save is pending the agent dispatch backend.", {
        description: "The stage config is ready — mutation wiring follows #54.",
        duration: 6000,
      });
    } finally {
      setSaving(false);
    }
  }

  return (
    <PageShell
      title="Workflow builder"
      description="Choose a pattern, configure stages, and save your agent workflow."
      actions={
        <Button onClick={handleSave} disabled={saving}>
          {saving ? (
            <Loader2Icon className="mr-1 h-4 w-4 animate-spin" />
          ) : (
            <SaveIcon className="mr-1 h-4 w-4" />
          )}
          Save workflow
        </Button>
      }
    >
      <div className="flex flex-col gap-8 max-w-5xl">
        {/* Metadata */}
        <Section title="1. Workflow metadata">
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="flex flex-col gap-1.5">
              <Label>Name</Label>
              <Input
                value={name}
                placeholder="e.g. PR Review Loop"
                onChange={(e) => setName(e.target.value)}
              />
            </div>
            <div className="flex flex-col gap-1.5">
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
        <Section title="2. Choose a pattern">
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

        <Separator />

        {/* Stage builder */}
        <Section
          title="3. Configure stages"
          action={
            <Button type="button" variant="outline" size="sm" onClick={addStage}>
              <PlusIcon className="mr-1 h-3.5 w-3.5" /> Add stage
            </Button>
          }
        >
          {stages.length === 0 ? (
            <EmptyState
              icon={<WorkflowIcon className="size-5" />}
              title="No stages yet"
              description="Add stages to define what this workflow does."
              secondary={
                <Button size="sm" onClick={addStage}>
                  <PlusIcon className="mr-1 h-3.5 w-3.5" /> Add first stage
                </Button>
              }
            />
          ) : (
            <div className="flex flex-col gap-3">
              {stages.map((stage, i) => (
                <StageCard
                  key={stage.id}
                  stage={stage}
                  index={i}
                  total={stages.length}
                  onChange={(patch) => patchStage(stage.id, patch)}
                  onRemove={() => removeStage(stage.id)}
                  onMoveUp={() => moveStage(i, -1)}
                  onMoveDown={() => moveStage(i, 1)}
                />
              ))}
            </div>
          )}
        </Section>
      </div>
    </PageShell>
  );
}
