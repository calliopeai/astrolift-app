"use client";

/**
 * Stage-based workflow definition builder (spec 40 §5.1, #970).
 *
 * The persisted model is an ORDERED pipeline with fan-out — not a free
 * DAG. Edges are always DERIVED from stage order + fan_out + aggregation;
 * nothing is user-drawable. Three views over the same ordered stage cards:
 *
 *  - **List**  — ordered stack, inline per-stage editors, up/down reorder.
 *  - **Graph** — React Flow render of the same cards; auto-layout from
 *    order via the shared rankLayout helper.
 *  - **Code**  — the canonical TOML manifest (exportWorkflowManifest /
 *    previewWorkflowManifest). In-place apply is intentionally disabled:
 *    TOML stages carry no stable identity, so edits cannot be mapped
 *    safely onto existing stage guids — Export + Import-as-new instead.
 *
 * Global (organization == null) and repository-managed definitions render
 * read-only with an ownership banner. All affordances are gated on the
 * server-authoritative `me.modules` workflows entry.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  ReactFlow,
  Controls,
  Background,
  MiniMap,
  Handle,
  Position,
  MarkerType,
  type Edge,
  type Node,
  type NodeTypes,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import {
  AlertTriangleIcon,
  ArrowDownIcon,
  ArrowUpIcon,
  BotIcon,
  CheckCircle2Icon,
  ChevronDownIcon,
  ChevronRightIcon,
  CodeIcon,
  CopyIcon,
  FileDownIcon,
  FileUpIcon,
  FlagIcon,
  ListIcon,
  Loader2Icon,
  LockIcon,
  MergeIcon,
  NetworkIcon,
  PlusIcon,
  SaveIcon,
  TrashIcon,
  UserCheckIcon,
  WorkflowIcon,
} from "lucide-react";
import { toast } from "sonner";

import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Section } from "@/components/ui/section";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { TagInput } from "@/components/ui/tag-input";
import { Textarea } from "@/components/ui/textarea";
import { rankLayout } from "@/components/viz/flow-layout";
import { AgentWorkloadPicker, SkillRefsPicker } from "@/components/workflows/pickers";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import {
  useCloneDefinition,
  useCreateWorkflowStage,
  useDeleteWorkflowStage,
  useManifestExport,
  useManifestImport,
  useManifestPreview,
  useReorderWorkflowStages,
  useUpdateWorkflowStage,
  useWorkflowDefinition,
  useWorkflowsEntitlement,
  useWorkflowStages,
} from "@/graphql/workflows/tiered.hooks";
import type {
  TieredMutationResult,
  WorkflowManifestPreview,
  WorkflowStage,
} from "@/graphql/workflows/tiered.types";

// ─── Domain constants ────────────────────────────────────────────────────

type StageKind = "agent_dispatch" | "human_gate" | "aggregation" | "checkpoint";

const STAGE_KINDS: {
  value: StageKind;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  hint: string;
}[] = [
  {
    value: "agent_dispatch",
    label: "Agent dispatch",
    icon: BotIcon,
    hint: "Dispatches an agent workload, optionally fanned out in parallel.",
  },
  {
    value: "human_gate",
    label: "Human gate",
    icon: UserCheckIcon,
    hint: "Pauses for human approval or rejection before proceeding.",
  },
  {
    value: "aggregation",
    label: "Aggregation",
    icon: MergeIcon,
    hint: "Collects and merges the outputs of the preceding fan-out stage.",
  },
  {
    value: "checkpoint",
    label: "Checkpoint",
    icon: FlagIcon,
    hint: "Snapshots state at this point — no external dispatch.",
  },
];

const ON_FAILURE_OPTIONS: { value: string; label: string }[] = [
  { value: "fail", label: "Fail workflow" },
  { value: "retry", label: "Retry" },
  { value: "skip", label: "Skip" },
  { value: "escalate", label: "Escalate" },
];

function stageKindMeta(kind: string) {
  return (
    STAGE_KINDS.find((k) => k.value === kind) ?? {
      value: kind as StageKind,
      label: kind,
      icon: WorkflowIcon,
      hint: "",
    }
  );
}

// ─── Draft model ─────────────────────────────────────────────────────────

type StageDraft = {
  kind: string;
  /** Also doubles as the checkpoint label. */
  role: string;
  agentRef: string;
  agentDefinitionGuid: string | null;
  environmentSpecSlug: string;
  skillRefs: string[];
  fanOutCount: number | null;
  onFailure: string;
  timeoutSeconds: number;
  prompt: string;
  outputKey: string;
  approvers: string[];
};

function parseSkillRefs(raw: unknown): string[] {
  if (!Array.isArray(raw)) return [];
  return raw.filter((s): s is string => typeof s === "string");
}

function draftFromStage(stage: WorkflowStage): StageDraft {
  return {
    kind: stage.kind,
    role: stage.role,
    agentRef: stage.agentRef,
    agentDefinitionGuid: stage.agentDefinitionGuid,
    environmentSpecSlug: stage.environmentSpecSlug,
    skillRefs: parseSkillRefs(stage.skillRefs),
    fanOutCount: stage.fanOutCount,
    onFailure: stage.onFailure,
    timeoutSeconds: stage.timeoutSeconds,
    prompt: stage.prompt,
    outputKey: stage.outputKey,
    approvers: parseSkillRefs(stage.approvers),
  };
}

function emptyDraft(): StageDraft {
  return {
    kind: "agent_dispatch",
    role: "",
    agentRef: "",
    agentDefinitionGuid: null,
    environmentSpecSlug: "",
    skillRefs: [],
    fanOutCount: null,
    onFailure: "fail",
    timeoutSeconds: 300,
    prompt: "",
    outputKey: "",
    approvers: [],
  };
}

function reportResult(
  result: TieredMutationResult | null | undefined,
  fallback: string
): boolean {
  if (result?.ok) return true;
  const errors = result?.errors ?? [];
  if (errors.length > 0) {
    for (const e of errors) toast.error(`${e.field}: ${e.messages.join(", ")}`);
  } else {
    toast.error(fallback);
  }
  return false;
}

// ─── Stage editor fields (shared: list card, graph node, add form) ──────

function StageEditorFields({
  draft,
  onPatch,
  orgScoped,
  disabled,
}: {
  draft: StageDraft;
  onPatch: (patch: Partial<StageDraft>) => void;
  orgScoped: string | null;
  disabled: boolean;
}) {
  const kindMeta = stageKindMeta(draft.kind);
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-col gap-1.5">
        <Label className="text-xs">Stage kind</Label>
        <Select
          value={draft.kind}
          onValueChange={(v) => onPatch({ kind: v })}
          disabled={disabled}
        >
          <SelectTrigger className="h-8 text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {STAGE_KINDS.map((k) => (
              <SelectItem key={k.value} value={k.value}>
                {k.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {kindMeta.hint && <p className="text-muted-foreground text-2xs">{kindMeta.hint}</p>}
      </div>

      {draft.kind === "agent_dispatch" && (
        <>
          <div className="flex flex-col gap-1.5">
            <Label className="text-xs">Role</Label>
            <Input
              value={draft.role}
              placeholder="e.g. reviewer"
              className="h-8 text-xs"
              disabled={disabled}
              onChange={(e) => onPatch({ role: e.target.value })}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label className="text-xs">Agent slug</Label>
            <Input
              value={draft.agentRef}
              placeholder="e.g. emr-bug-triage"
              className="h-8 text-xs"
              disabled={disabled}
              onChange={(e) => onPatch({ agentRef: e.target.value })}
            />
            <p className="text-muted-foreground text-2xs">
              Declarative local reference; the selected workload wins when both are set.
            </p>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label className="text-xs">Agent workload</Label>
            <AgentWorkloadPicker
              value={draft.agentDefinitionGuid}
              onChange={(id) => onPatch({ agentDefinitionGuid: id })}
              orgScoped={orgScoped}
              disabled={disabled}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label className="text-xs">Environment spec slug</Label>
            <Input
              value={draft.environmentSpecSlug}
              placeholder="Defaults to the agent workload slug"
              className="h-8 text-xs"
              disabled={disabled}
              onChange={(e) => onPatch({ environmentSpecSlug: e.target.value })}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label className="text-xs">Skills</Label>
            <SkillRefsPicker
              value={draft.skillRefs}
              onChange={(refs) => onPatch({ skillRefs: refs })}
              orgScoped={orgScoped}
              disabled={disabled}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label className="text-xs">Stage instructions</Label>
            <Textarea
              value={draft.prompt}
              placeholder="Instructions added to this stage's immutable task packet"
              className="min-h-16 text-xs"
              disabled={disabled}
              onChange={(e) => onPatch({ prompt: e.target.value })}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label className="text-xs">Fan-out count</Label>
            <Input
              type="number"
              min={0}
              value={draft.fanOutCount ?? ""}
              placeholder="0 (no fan-out)"
              className="h-8 text-xs"
              disabled={disabled}
              onChange={(e) => {
                const n = parseInt(e.target.value, 10);
                onPatch({ fanOutCount: Number.isNaN(n) ? null : n });
              }}
            />
            <p className="text-muted-foreground text-2xs">
              Runs N parallel copies of this stage; merge them with a later aggregation stage.
            </p>
          </div>
        </>
      )}

      {draft.kind === "human_gate" && (
        <>
          <div className="flex flex-col gap-1.5">
            <Label className="text-xs">Prompt</Label>
            <Textarea
              value={draft.prompt}
              placeholder="What should the approver decide?"
              className="min-h-16 text-xs"
              disabled={disabled}
              onChange={(e) => onPatch({ prompt: e.target.value })}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label className="text-xs">Approvers</Label>
            <TagInput
              value={draft.approvers}
              onChange={(tags) => onPatch({ approvers: tags })}
              placeholder="Type an approver and press Enter"
              disabled={disabled}
            />
          </div>
        </>
      )}

      <div className="flex flex-col gap-1.5">
        <Label className="text-xs">Output key</Label>
        <Input
          value={draft.outputKey}
          placeholder="Defaults to stage_<order>"
          className="h-8 text-xs"
          disabled={disabled}
          onChange={(e) => onPatch({ outputKey: e.target.value })}
        />
      </div>

      {draft.kind === "checkpoint" && (
        <div className="flex flex-col gap-1.5">
          <Label className="text-xs">Label</Label>
          <Input
            value={draft.role}
            placeholder="e.g. after-triage"
            className="h-8 text-xs"
            disabled={disabled}
            onChange={(e) => onPatch({ role: e.target.value })}
          />
        </div>
      )}

      <div className="grid grid-cols-2 gap-3">
        <div className="flex flex-col gap-1.5">
          <Label className="text-xs">On failure</Label>
          <Select
            value={draft.onFailure}
            onValueChange={(v) => onPatch({ onFailure: v })}
            disabled={disabled}
          >
            <SelectTrigger className="h-8 text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {ON_FAILURE_OPTIONS.map((o) => (
                <SelectItem key={o.value} value={o.value}>
                  {o.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="flex flex-col gap-1.5">
          <Label className="text-xs">Timeout (seconds)</Label>
          <Input
            type="number"
            min={1}
            max={86400}
            value={draft.timeoutSeconds}
            className="h-8 text-xs"
            disabled={disabled}
            onChange={(e) =>
              onPatch({ timeoutSeconds: parseInt(e.target.value, 10) || 300 })
            }
          />
        </div>
      </div>
    </div>
  );
}

// ─── Stage card (one node — collapsed header + expandable editor) ───────

type StageCardProps = {
  stage: WorkflowStage;
  index: number;
  total: number;
  orgScoped: string | null;
  readOnly: boolean;
  expanded: boolean;
  saving: boolean;
  onToggle: (guid: string) => void;
  onSave: (guid: string, draft: StageDraft) => Promise<void>;
  onDelete: (guid: string) => void;
  onMove: (index: number, dir: -1 | 1) => void;
  inGraph?: boolean;
};

function StageCard({
  stage,
  index,
  total,
  orgScoped,
  readOnly,
  expanded,
  saving,
  onToggle,
  onSave,
  onDelete,
  onMove,
  inGraph = false,
}: StageCardProps) {
  const [draft, setDraft] = useState<StageDraft>(() => draftFromStage(stage));
  const meta = stageKindMeta(stage.kind);
  const KindIcon = meta.icon;

  return (
    <Card className={inGraph ? "w-80 shadow-md" : undefined}>
      <CardContent className={inGraph ? "nodrag nowheel flex flex-col gap-3 p-4" : "flex flex-col gap-3"}>
        <div className="flex items-center gap-2">
          <button
            type="button"
            className="text-muted-foreground hover:text-foreground rounded p-0.5"
            onClick={() => onToggle(stage.guid)}
            aria-label={expanded ? "Collapse stage" : "Expand stage"}
          >
            {expanded ? (
              <ChevronDownIcon className="h-4 w-4" />
            ) : (
              <ChevronRightIcon className="h-4 w-4" />
            )}
          </button>
          <span className="text-muted-foreground text-xs font-medium tabular-nums">
            {index + 1}
          </span>
          <KindIcon className="text-muted-foreground h-4 w-4 shrink-0" />
          <div className="flex min-w-0 flex-1 items-center gap-2">
            <span className="truncate text-sm font-medium">{meta.label}</span>
            {stage.agentDefinitionName && (
              <span className="text-muted-foreground truncate text-xs">
                {stage.agentDefinitionName}
              </span>
            )}
          </div>
          <div className="flex shrink-0 items-center gap-1">
            {stage.fanOutCount != null && stage.fanOutCount > 1 && (
              <Badge variant="secondary" className="text-2xs">
                ×{stage.fanOutCount}
              </Badge>
            )}
            {stage.onFailure !== "fail" && (
              <Badge variant="outline" className="text-2xs">
                {stage.onFailure}
              </Badge>
            )}
            <Badge variant="outline" className="text-2xs">
              {stage.timeoutSeconds}s
            </Badge>
          </div>
          {!readOnly && (
            <div className="flex shrink-0 items-center">
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className="h-6 w-6"
                disabled={index === 0 || saving}
                onClick={() => onMove(index, -1)}
                title="Move up"
              >
                <ArrowUpIcon className="h-3 w-3" />
              </Button>
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className="h-6 w-6"
                disabled={index === total - 1 || saving}
                onClick={() => onMove(index, 1)}
                title="Move down"
              >
                <ArrowDownIcon className="h-3 w-3" />
              </Button>
            </div>
          )}
        </div>

        {expanded && (
          <>
            <Separator />
            <StageEditorFields
              draft={draft}
              onPatch={(patch) => setDraft((d) => ({ ...d, ...patch }))}
              orgScoped={orgScoped}
              disabled={readOnly || saving}
            />
            {!readOnly && (
              <div className="flex items-center justify-between">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="text-destructive hover:bg-destructive/10 hover:text-destructive"
                  disabled={saving}
                  onClick={() => onDelete(stage.guid)}
                >
                  <TrashIcon className="mr-1 h-3 w-3" /> Delete
                </Button>
                <Button
                  type="button"
                  size="sm"
                  disabled={saving}
                  onClick={() => onSave(stage.guid, draft)}
                >
                  {saving ? (
                    <Loader2Icon className="mr-1 h-3 w-3 animate-spin" />
                  ) : (
                    <SaveIcon className="mr-1 h-3 w-3" />
                  )}
                  Save stage
                </Button>
              </div>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}

// ─── Graph node wrapper ──────────────────────────────────────────────────

function StageFlowNode({ data }: { data: StageCardProps }) {
  return (
    <div>
      <Handle type="target" position={Position.Left} className="!bg-muted-foreground" />
      <StageCard {...data} inGraph />
      <Handle type="source" position={Position.Right} className="!bg-muted-foreground" />
    </div>
  );
}

const stageNodeTypes: NodeTypes = {
  stageNode: StageFlowNode as unknown as NodeTypes[string],
};

// ─── Add-stage form ──────────────────────────────────────────────────────

function AddStageCard({
  orgScoped,
  creating,
  onCreate,
  onCancel,
}: {
  orgScoped: string | null;
  creating: boolean;
  onCreate: (draft: StageDraft) => Promise<void>;
  onCancel: () => void;
}) {
  const [draft, setDraft] = useState<StageDraft>(emptyDraft);
  return (
    <Card className="border-dashed">
      <CardContent className="flex flex-col gap-3">
        <div className="flex items-center gap-2">
          <PlusIcon className="text-muted-foreground h-4 w-4" />
          <span className="text-sm font-medium">New stage</span>
        </div>
        <Separator />
        <StageEditorFields
          draft={draft}
          onPatch={(patch) => setDraft((d) => ({ ...d, ...patch }))}
          orgScoped={orgScoped}
          disabled={creating}
        />
        <div className="flex items-center justify-end gap-2">
          <Button type="button" variant="ghost" size="sm" disabled={creating} onClick={onCancel}>
            Cancel
          </Button>
          <Button type="button" size="sm" disabled={creating} onClick={() => onCreate(draft)}>
            {creating ? (
              <Loader2Icon className="mr-1 h-3 w-3 animate-spin" />
            ) : (
              <PlusIcon className="mr-1 h-3 w-3" />
            )}
            Add stage
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

// ─── Code view (TOML manifest) ───────────────────────────────────────────

function CodeView({
  slug,
  orgId,
  canCreate,
}: {
  slug: string;
  orgId: string | null;
  canCreate: boolean;
}) {
  const router = useRouter();
  // null = "not user-edited yet" — the export result seeds the editor.
  const [editedToml, setEditedToml] = useState<string | null>(null);
  const [preview, setPreview] = useState<WorkflowManifestPreview | null>(null);
  const [validating, setValidating] = useState(false);
  const [importing, setImporting] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const [exportManifest, exportResult] = useManifestExport();
  const [previewManifest] = useManifestPreview();
  const [importManifest] = useManifestImport();

  // Fetch the canonical TOML once per definition.
  useEffect(() => {
    void exportManifest({ variables: { definitionSlug: slug } });
  }, [exportManifest, slug]);

  const exported = exportResult.data?.exportWorkflowManifest;
  const toml = editedToml ?? (exported?.ok ? (exported.toml ?? "") : "");
  const loading = editedToml == null && !exported && (exportResult.loading || !exportResult.called);
  const exportFailed =
    editedToml == null &&
    ((exported != null && !exported.ok) || (exported == null && exportResult.error != null));
  const ready = !loading && !exportFailed;

  const setToml = (value: string) => setEditedToml(value);

  const handleValidate = async (source?: string) => {
    setValidating(true);
    try {
      const { data } = await previewManifest({ variables: { toml: source ?? toml } });
      setPreview(data?.previewWorkflowManifest ?? null);
    } finally {
      setValidating(false);
    }
  };

  const handleDownload = () => {
    const blob = new Blob([toml], { type: "application/toml" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${slug}.toml`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const handleUpload = async (file: File) => {
    const text = await file.text();
    setToml(text);
    await handleValidate(text);
  };

  const handleImportAsNew = async () => {
    setImporting(true);
    try {
      const { data } = await importManifest({
        variables: { toml, preview: false, orgId },
      });
      const res = data?.importWorkflowManifest;
      if (res?.ok && res.createdSlug) {
        toast.success("Definition imported", { description: res.createdSlug });
        router.push(`/workflows/${res.createdSlug}/builder`);
        return;
      }
      if (res?.manifest && !res.manifest.ok && res.manifest.error) {
        setPreview(res.manifest);
      }
      reportResult(res, "Import failed");
    } finally {
      setImporting(false);
    }
  };

  return (
    <Section
      title="Manifest (TOML)"
      description="The canonical, versionable form of this definition."
      action={
        <div className="flex flex-wrap items-center gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={validating || !ready}
            onClick={() => handleValidate()}
          >
            {validating ? (
              <Loader2Icon className="mr-1 h-3.5 w-3.5 animate-spin" />
            ) : (
              <CheckCircle2Icon className="mr-1 h-3.5 w-3.5" />
            )}
            Validate
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={!ready}
            onClick={handleDownload}
          >
            <FileDownIcon className="mr-1 h-3.5 w-3.5" /> Export .toml
          </Button>
          <Button type="button" variant="outline" size="sm" onClick={() => fileRef.current?.click()}>
            <FileUpIcon className="mr-1 h-3.5 w-3.5" /> Upload .toml
          </Button>
          <Button
            type="button"
            size="sm"
            disabled={!canCreate || importing || !ready}
            onClick={handleImportAsNew}
            title={canCreate ? undefined : "You don't have create access"}
          >
            {importing ? (
              <Loader2Icon className="mr-1 h-3.5 w-3.5 animate-spin" />
            ) : (
              <CopyIcon className="mr-1 h-3.5 w-3.5" />
            )}
            Import as new
          </Button>
          <input
            ref={fileRef}
            type="file"
            accept=".toml,text/plain"
            className="hidden"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) void handleUpload(f);
              e.target.value = "";
            }}
          />
        </div>
      }
    >
      {loading ? (
        <div className="flex items-center justify-center py-12">
          <Loader2Icon className="text-muted-foreground h-5 w-5 animate-spin" />
        </div>
      ) : exportFailed ? (
        <div className="border-danger/40 bg-danger/10 flex items-start gap-2 rounded-md border px-3 py-2 text-sm">
          <AlertTriangleIcon className="text-danger-fg mt-0.5 h-4 w-4 shrink-0" />
          <p>{exported?.error ?? exportResult.error?.message ?? "Failed to export the manifest."}</p>
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          <Textarea
            value={toml}
            onChange={(e) => {
              setToml(e.target.value);
              setPreview(null);
            }}
            spellCheck={false}
            className="min-h-96 font-mono text-xs"
            aria-label="Workflow manifest TOML"
          />
          {preview && !preview.ok && (
            <div className="border-danger/40 bg-danger/10 flex items-start gap-2 rounded-md border px-3 py-2 text-sm">
              <AlertTriangleIcon className="text-danger-fg mt-0.5 h-4 w-4 shrink-0" />
              <div className="min-w-0">
                <p className="font-medium">Invalid manifest</p>
                <p className="text-muted-foreground text-xs">
                  {preview.error}
                  {preview.errorLine != null && (
                    <span>
                      {" "}
                      (line {preview.errorLine}
                      {preview.errorColumn != null ? `, col ${preview.errorColumn}` : ""})
                    </span>
                  )}
                  {preview.errorPath && <span className="font-mono"> at {preview.errorPath}</span>}
                </p>
              </div>
            </div>
          )}
          {preview?.ok && preview.definition && (
            <div className="border-success/40 bg-success/10 flex items-start gap-2 rounded-md border px-3 py-2 text-sm">
              <CheckCircle2Icon className="text-success-fg mt-0.5 h-4 w-4 shrink-0" />
              <p>
                Valid — <span className="font-medium">{preview.definition.name}</span> (
                {preview.definition.pattern}), {preview.stages.length} stage
                {preview.stages.length === 1 ? "" : "s"}.
              </p>
            </div>
          )}
          <p className="text-muted-foreground text-xs">
            Applying code edits to this definition in place isn&apos;t supported yet — TOML
            stages carry no stable identity, so edits can&apos;t be mapped safely onto existing
            stages. Export, edit, and use &quot;Import as new&quot; to create a definition from
            the edited manifest.
          </p>
        </div>
      )}
    </Section>
  );
}

// ─── Main builder ────────────────────────────────────────────────────────

type BuilderView = "list" | "graph" | "code";

const VIEW_OPTIONS: { value: BuilderView; label: string; icon: React.ComponentType<{ className?: string }> }[] = [
  { value: "list", label: "List", icon: ListIcon },
  { value: "graph", label: "Graph", icon: NetworkIcon },
  { value: "code", label: "Code", icon: CodeIcon },
];

export function StageBuilder({ slug }: { slug: string }) {
  const router = useRouter();
  const { org } = useActiveOrg();
  const orgId = org?.id ?? null;
  const { canCreate, canManage } = useWorkflowsEntitlement();

  const { definition, loading: defLoading } = useWorkflowDefinition(slug, orgId);
  const { stages, loading: stagesLoading, refetch: refetchStages } = useWorkflowStages(slug);

  const [view, setView] = useState<BuilderView>("list");
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [adding, setAdding] = useState(false);
  const [busyGuid, setBusyGuid] = useState<string | null>(null);

  const [createStage, { loading: creating }] = useCreateWorkflowStage();
  const [updateStage] = useUpdateWorkflowStage();
  const [deleteStage] = useDeleteWorkflowStage();
  const [reorderStages] = useReorderWorkflowStages();
  const [cloneDefinition, { loading: cloning }] = useCloneDefinition();

  const sorted = useMemo(() => [...stages].sort((a, b) => a.order - b.order), [stages]);

  const isGlobal = definition != null && (definition.isGlobal || definition.organizationGuid == null);
  const isSourceManaged = Boolean(definition?.sourceRepo);
  const readOnly = isGlobal || isSourceManaged || !canManage;

  const toggleExpanded = useCallback((guid: string) => {
    setExpanded((prev) => ({ ...prev, [guid]: !prev[guid] }));
  }, []);

  const handleSaveStage = useCallback(
    async (guid: string, draft: StageDraft) => {
      setBusyGuid(guid);
      try {
        const { data } = await updateStage({
          variables: {
            stageGuid: guid,
            kind: draft.kind,
            role: draft.role.trim() !== "" ? draft.role.trim() : null,
            onFailure: draft.onFailure,
            timeoutSeconds: draft.timeoutSeconds,
            agentDefinitionGuid: draft.agentDefinitionGuid,
            agentRef: draft.agentRef.trim(),
            environmentSpecSlug: draft.environmentSpecSlug.trim(),
            skillRefs: draft.skillRefs,
            fanOutCount: draft.fanOutCount,
            prompt: draft.prompt,
            outputKey: draft.outputKey.trim(),
            approvers: draft.approvers,
          },
        });
        if (reportResult(data?.updateWorkflowStage, "Failed to save the stage")) {
          toast.success("Stage saved");
          await refetchStages();
        }
      } finally {
        setBusyGuid(null);
      }
    },
    [updateStage, refetchStages]
  );

  const handleDeleteStage = useCallback(
    async (guid: string) => {
      setBusyGuid(guid);
      try {
        const { data } = await deleteStage({ variables: { stageGuid: guid } });
        if (reportResult(data?.deleteWorkflowStage, "Failed to delete the stage")) {
          toast.success("Stage deleted");
          await refetchStages();
        }
      } finally {
        setBusyGuid(null);
      }
    },
    [deleteStage, refetchStages]
  );

  const handleMoveStage = useCallback(
    async (index: number, dir: -1 | 1) => {
      const target = index + dir;
      if (target < 0 || target >= sorted.length) return;
      const guids = sorted.map((s) => s.guid);
      [guids[index], guids[target]] = [guids[target], guids[index]];
      const { data } = await reorderStages({
        variables: { definitionSlug: slug, stageGuids: guids },
      });
      if (reportResult(data?.reorderWorkflowStages, "Failed to reorder stages")) {
        await refetchStages();
      }
    },
    [sorted, reorderStages, slug, refetchStages]
  );

  const handleCreateStage = useCallback(
    async (draft: StageDraft) => {
      const nextOrder =
        sorted.length > 0 ? Math.max(...sorted.map((s) => s.order)) + 1 : 0;
      const { data } = await createStage({
        variables: {
          workflowSlug: slug,
          kind: draft.kind,
          order: nextOrder,
          role: draft.role.trim() !== "" ? draft.role.trim() : null,
          onFailure: draft.onFailure,
          timeoutSeconds: draft.timeoutSeconds,
          agentDefinitionGuid: draft.agentDefinitionGuid,
          agentRef: draft.agentRef.trim() || null,
          environmentSpecSlug: draft.environmentSpecSlug.trim() || null,
          skillRefs: draft.skillRefs,
          fanOutCount: draft.fanOutCount,
          prompt: draft.prompt.trim() !== "" ? draft.prompt : null,
          outputKey: draft.outputKey.trim() || null,
          approvers: draft.approvers.length > 0 ? draft.approvers : null,
        },
      });
      if (reportResult(data?.createWorkflowStage, "Failed to add the stage")) {
        toast.success("Stage added");
        setAdding(false);
        await refetchStages();
      }
    },
    [createStage, slug, sorted, refetchStages]
  );

  const handleClone = useCallback(async () => {
    const { data } = await cloneDefinition({ variables: { slug, orgId } });
    const res = data?.cloneWorkflowDefinition;
    if (res?.ok && res.slug) {
      toast.success("Template cloned", { description: res.slug });
      router.push(`/workflows/${res.slug}/builder`);
      return;
    }
    reportResult(res, "Failed to clone the template");
  }, [cloneDefinition, slug, orgId, router]);

  // Graph derivation: nodes from stage order (rankLayout over the ordered
  // chain), edges strictly consecutive — fan-out and aggregation annotate
  // the derived edges, they never add user-drawable ones.
  const { flowNodes, flowEdges } = useMemo(() => {
    const ids = sorted.map((s) => s.guid);
    const chain = ids.slice(1).map((id, i) => ({ source: ids[i], target: id }));
    const pos = rankLayout(ids, chain, { colWidth: 400, rowHeight: 260 });
    const nodes: Node[] = sorted.map((stage, index) => ({
      id: stage.guid,
      type: "stageNode",
      position: pos.get(stage.guid) ?? { x: index * 400, y: 0 },
      draggable: false,
      data: {
        stage,
        index,
        total: sorted.length,
        orgScoped: orgId,
        readOnly,
        expanded: !!expanded[stage.guid],
        saving: busyGuid === stage.guid,
        onToggle: toggleExpanded,
        onSave: handleSaveStage,
        onDelete: handleDeleteStage,
        onMove: handleMoveStage,
      } satisfies StageCardProps,
    }));
    const edges: Edge[] = sorted.slice(1).map((stage, i) => {
      const prev = sorted[i];
      const fanned = prev.fanOutCount != null && prev.fanOutCount > 1;
      return {
        id: `${prev.guid}-${stage.guid}`,
        source: prev.guid,
        target: stage.guid,
        animated: fanned,
        label: fanned
          ? `fan-out ×${prev.fanOutCount}`
          : stage.kind === "aggregation"
            ? "merge"
            : undefined,
        markerEnd: { type: MarkerType.ArrowClosed },
        style: { strokeWidth: 2 },
      };
    });
    return { flowNodes: nodes, flowEdges: edges };
  }, [
    sorted,
    orgId,
    readOnly,
    expanded,
    busyGuid,
    toggleExpanded,
    handleSaveStage,
    handleDeleteStage,
    handleMoveStage,
  ]);

  if (defLoading && !definition) {
    return (
      <div className="flex flex-1 items-center justify-center p-6">
        <Loader2Icon className="text-muted-foreground h-6 w-6 animate-spin" />
      </div>
    );
  }

  if (!definition) {
    return (
      <PageShell title="Workflow definition not found">
        <EmptyState
          icon={<WorkflowIcon className="size-5" />}
          title="Definition not found"
          description={`The workflow definition "${slug}" may have been deleted, or you may not have access to it.`}
          actionHref="/workflows"
          actionLabel="Back to workflows"
        />
      </PageShell>
    );
  }

  return (
    <PageShell
      title={`${definition.name} — Builder`}
      description={definition.description || `Ordered stage pipeline (${definition.patternKind}).`}
      actions={
        <div className="flex items-center gap-1 rounded-md border p-0.5">
          {VIEW_OPTIONS.map((opt) => {
            const OptIcon = opt.icon;
            return (
              <Button
                key={opt.value}
                type="button"
                variant={view === opt.value ? "secondary" : "ghost"}
                size="sm"
                className="h-7 px-2.5"
                onClick={() => setView(opt.value)}
              >
                <OptIcon className="mr-1 h-3.5 w-3.5" />
                {opt.label}
              </Button>
            );
          })}
        </div>
      }
    >
      {isGlobal && (
        <div className="border-info/40 bg-info/10 flex flex-wrap items-center justify-between gap-3 rounded-md border px-4 py-3">
          <div className="flex items-center gap-2 text-sm">
            <LockIcon className="text-info-fg h-4 w-4 shrink-0" />
            <span>
              <span className="font-medium">Platform template</span> — read-only. Clone it to
              create an editable copy in your organization.
            </span>
          </div>
          <Button
            type="button"
            size="sm"
            disabled={!canCreate || cloning}
            onClick={handleClone}
            title={canCreate ? undefined : "You don't have create access"}
          >
            {cloning ? (
              <Loader2Icon className="mr-1 h-3.5 w-3.5 animate-spin" />
            ) : (
              <CopyIcon className="mr-1 h-3.5 w-3.5" />
            )}
            Clone to edit
          </Button>
        </div>
      )}

      {isSourceManaged && (
        <div className="border-info/40 bg-info/10 flex items-center gap-2 rounded-md border px-4 py-3 text-sm">
          <LockIcon className="text-info-fg h-4 w-4 shrink-0" />
          <span>
            <span className="font-medium">Repository managed</span> — edit{" "}
            <code>{definition.sourceRepo}/{definition.sourcePath}</code> and sync the agent repository.
            {definition.sourceRef ? ` Last reconciled at ${definition.sourceRef.slice(0, 12)}.` : ""}
          </span>
        </div>
      )}

      {view === "code" ? (
        <CodeView slug={slug} orgId={orgId} canCreate={canCreate} />
      ) : (
        <Section
          title="Stages"
          description="An ordered pipeline — stages run in sequence; fan-out runs parallel copies merged by a later aggregation stage."
          action={
            !readOnly && !adding ? (
              <Button type="button" variant="outline" size="sm" onClick={() => setAdding(true)}>
                <PlusIcon className="mr-1 h-3.5 w-3.5" /> Add stage
              </Button>
            ) : undefined
          }
        >
          {stagesLoading && sorted.length === 0 ? (
            <div className="flex items-center justify-center py-12">
              <Loader2Icon className="text-muted-foreground h-5 w-5 animate-spin" />
            </div>
          ) : sorted.length === 0 && !adding ? (
            <EmptyState
              icon={<WorkflowIcon className="size-5" />}
              title="No stages yet"
              description={
                readOnly
                  ? "This definition has no stages."
                  : "Add stages to define what this workflow does."
              }
              secondary={
                !readOnly ? (
                  <Button size="sm" onClick={() => setAdding(true)}>
                    <PlusIcon className="mr-1 h-3.5 w-3.5" /> Add first stage
                  </Button>
                ) : undefined
              }
            />
          ) : view === "graph" ? (
            <div className="flex flex-col gap-3">
              <div className="bg-card h-[560px] rounded-lg border">
                <ReactFlow
                  nodes={flowNodes}
                  edges={flowEdges}
                  nodeTypes={stageNodeTypes}
                  nodesDraggable={false}
                  nodesConnectable={false}
                  fitView
                  className="bg-dots-pattern"
                >
                  <Controls />
                  <Background />
                  <MiniMap />
                </ReactFlow>
              </div>
              <p className="text-muted-foreground text-xs">
                Edges are derived from stage order, fan-out and aggregation — the pipeline is
                ordered, not a free-form graph. Expand a stage card to edit it.
              </p>
            </div>
          ) : (
            <div className="flex flex-col gap-3">
              {sorted.map((stage, index) => (
                <StageCard
                  key={stage.guid}
                  stage={stage}
                  index={index}
                  total={sorted.length}
                  orgScoped={orgId}
                  readOnly={readOnly}
                  expanded={!!expanded[stage.guid]}
                  saving={busyGuid === stage.guid}
                  onToggle={toggleExpanded}
                  onSave={handleSaveStage}
                  onDelete={handleDeleteStage}
                  onMove={handleMoveStage}
                />
              ))}
            </div>
          )}

          {adding && (
            <AddStageCard
              orgScoped={orgId}
              creating={creating}
              onCreate={handleCreateStage}
              onCancel={() => setAdding(false)}
            />
          )}
        </Section>
      )}
    </PageShell>
  );
}
