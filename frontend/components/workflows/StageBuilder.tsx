"use client";

import { useTranslations } from "next-intl";

/**
 * Stage-based workflow definition builder (spec 40 §5.1, #970), the
 * workflow's Builder tab (spec 44 §5.2).
 *
 * The persisted model is an ORDERED pipeline with fan-out, not a free DAG.
 * Edges are always DERIVED from stage order + fan_out + aggregation;
 * nothing is user-drawable. Two views over the same ordered stages:
 *
 *  - **Stages**: the workflow drawn in the person's chosen workflow view
 *    (WorkflowView: loops, fan-out, supervisors and nested workflows from
 *    definition-line.ts) beside the ordered stage cards. One card is open
 *    at a time, picked in the list or by clicking its station, so the
 *    page stays within about two screens.
 *  - **Code**: the canonical TOML manifest (exportWorkflowManifest /
 *    previewWorkflowManifest). In-place apply is intentionally disabled:
 *    TOML stages carry no stable identity, so edits cannot be mapped
 *    safely onto existing stage guids; Export + Import-as-new instead.
 *
 * Global (organization == null) and repository-managed definitions render
 * read-only with an ownership banner. All affordances are gated on the
 * server-authoritative `me.modules` workflows entry.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
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
  PlusIcon,
  SaveIcon,
  TrashIcon,
  UserCheckIcon,
  WorkflowIcon,
} from "lucide-react";

import { BoundedStageOptions } from "./BoundedStageOptions";
import { CollectionStageOptions } from "./CollectionStageOptions";

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
import { WorkflowView } from "@/components/viz/WorkflowView";
import { AgentWorkloadPicker, SkillRefsPicker } from "@/components/workflows/pickers";

import { definitionLine, definitionSnapshot, lineShape } from "./definition-line";

import type { useStageBuilder } from "./use-stage-builder";
import type { WorkflowManifestPreview, WorkflowStage } from "@/graphql/workflows/tiered.types";

// ─── Domain constants ────────────────────────────────────────────────────

type StageKind = "agent_dispatch" | "human_gate" | "aggregation" | "checkpoint" | "workflow";

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
    value: "workflow",
    label: "Nested workflow",
    icon: WorkflowIcon,
    hint: "Runs another visible workflow as a linked child run.",
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
    hint: "Snapshots state at this point, with no external dispatch.",
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

export type StageDraft = {
  kind: string;
  /** Also doubles as the checkpoint label. */
  role: string;
  agentRef: string;
  workflowRef: string;
  agentDefinitionGuid: string | null;
  environmentSpecSlug: string;
  skillRefs: string[];
  fanOutCount: number | null;
  onFailure: string;
  timeoutSeconds: number;
  maxAttempts?: number;
  backEdge?: unknown;
  iteration?: unknown;
  backEdgeValueJson?: string;
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
    workflowRef: stage.workflowRef,
    agentDefinitionGuid: stage.agentDefinitionGuid,
    environmentSpecSlug: stage.environmentSpecSlug,
    skillRefs: parseSkillRefs(stage.skillRefs),
    fanOutCount: stage.fanOutCount,
    onFailure: stage.onFailure,
    timeoutSeconds: stage.timeoutSeconds,
    maxAttempts: stage.maxAttempts ?? 3,
    backEdge: stage.backEdge ?? {},
    iteration: stage.iteration ?? {},
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
    workflowRef: "",
    agentDefinitionGuid: null,
    environmentSpecSlug: "",
    skillRefs: [],
    fanOutCount: null,
    onFailure: "fail",
    timeoutSeconds: 300,
    maxAttempts: 3,
    backEdge: {},
    iteration: {},
    prompt: "",
    outputKey: "",
    approvers: [],
  };
}

// ─── Stage editor fields (shared: list card, graph node, add form) ──────

type PickerOptions = ReturnType<typeof useStageBuilder>["pickerOptions"];

// The pickers' options, provided once by StageBuilder so the stage cards
// and the add form all read the same listings.
const PickerOptionsContext = createContext<PickerOptions>({
  workloads: [],
  workloadsLoading: false,
  skills: [],
  skillsLoading: false,
});

const StageTargetsContext = createContext<WorkflowStage[]>([]);

function StageEditorFields({
  draft,
  onPatch,
  orgScoped,
  disabled,
  stageOrder = Infinity,
}: {
  stageOrder?: number;
  draft: StageDraft;
  onPatch: (patch: Partial<StageDraft>) => void;
  orgScoped: string | null;
  disabled: boolean;
}) {
  const t = useTranslations("workflowCollections");
  const followingKeys = useContext(StageTargetsContext)
    .filter((stage) => stage.order > stageOrder && stage.outputKey)
    .map((stage) => stage.outputKey);
  const previousKeys = useContext(StageTargetsContext)
    .filter((stage) => stage.order < stageOrder && stage.outputKey)
    .map((stage) => stage.outputKey);
  const kindMeta = {
    ...stageKindMeta(draft.kind),
    ...(draft.kind === "collection"
      ? { label: t("collectionKind") }
      : draft.kind === "format_record"
        ? { label: t("formatKind") }
        : {}),
  };
  const options = useContext(PickerOptionsContext);
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-col gap-1.5">
        <Label className="text-xs">Stage kind</Label>
        <Select
          value={draft.kind}
          onValueChange={(v) =>
            onPatch({
              kind: v,
              workflowRef: v === "workflow" ? draft.workflowRef : "",
              iteration:
                v === draft.kind
                  ? draft.iteration
                  : v === "collection"
                    ? { max_items: 10, body_end: followingKeys[0] ?? "", items_path: "items" }
                    : v === "format_record"
                      ? { source_format: "langflow_parser", pattern: "{text}", separator: "\n" }
                      : {},
            })
          }
          disabled={disabled}
        >
          <SelectTrigger className="h-8 text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {(draft.kind === "collection" || followingKeys.length > 0
              ? [...STAGE_KINDS, { value: "collection", label: t("collectionKind") }]
              : STAGE_KINDS
            )
              .concat([{ value: "format_record", label: t("formatKind") }])
              .map((k) => (
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
              workloads={options.workloads}
              loading={options.workloadsLoading}
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
              options={options.skills}
              loading={options.skillsLoading}
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

      {draft.kind === "workflow" && (
        <div className="flex flex-col gap-1.5">
          <Label className="text-xs">Child workflow slug</Label>
          <Input
            value={draft.workflowRef}
            placeholder="e.g. emr-triage-patch"
            className="h-8 text-xs"
            disabled={disabled}
            onChange={(e) => onPatch({ workflowRef: e.target.value })}
          />
          <p className="text-muted-foreground text-2xs">
            Must be in this project or a reusable org/global definition. Cycles and depth over eight
            are rejected.
          </p>
        </div>
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
        {["agent_dispatch", "workflow"].includes(draft.kind) && (
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
        )}
        <div className="flex flex-col gap-1.5">
          <Label className="text-xs">Timeout (seconds)</Label>
          <Input
            type="number"
            min={1}
            max={86400}
            value={draft.timeoutSeconds}
            className="h-8 text-xs"
            disabled={disabled}
            onChange={(e) => onPatch({ timeoutSeconds: parseInt(e.target.value, 10) || 300 })}
          />
        </div>
      </div>
      <CollectionStageOptions
        kind={draft.kind}
        iteration={draft.iteration ?? {}}
        targets={followingKeys}
        disabled={disabled}
        onChange={(iteration) => onPatch({ iteration })}
      />
      <BoundedStageOptions
        kind={draft.kind}
        maxAttempts={draft.maxAttempts ?? 3}
        backEdge={draft.backEdge ?? {}}
        valueJson={draft.backEdgeValueJson}
        targets={previousKeys}
        disabled={disabled}
        onChange={onPatch}
      />
    </div>
  );
}

// ─── Stage card (collapsed header + expandable editor) ─────────────────

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
}: StageCardProps) {
  const [draft, setDraft] = useState<StageDraft>(() => draftFromStage(stage));
  const t = useTranslations("workflowCollections");
  const meta = {
    ...stageKindMeta(stage.kind),
    ...(stage.kind === "collection"
      ? { label: t("collectionKind") }
      : stage.kind === "format_record"
        ? { label: t("formatKind") }
        : {}),
  };
  const KindIcon = meta.icon;

  return (
    <Card className={expanded ? "border-primary/60" : undefined}>
      <CardContent className="flex min-w-0 flex-col gap-3">
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
            {stage.kind === "workflow" && stage.workflowRef && (
              <span className="text-muted-foreground truncate text-xs">{stage.workflowRef}</span>
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
              stageOrder={stage.order}
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
  canCreate,
  manifest,
}: {
  slug: string;
  canCreate: boolean;
  manifest: StageBuilderProps["manifest"];
}) {
  // null = "not user-edited yet": the export result seeds the editor.
  const [editedToml, setEditedToml] = useState<string | null>(null);
  const [preview, setPreview] = useState<WorkflowManifestPreview | null>(null);
  const [validating, setValidating] = useState(false);
  const [importing, setImporting] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const { load, exported } = manifest;

  // Fetch the canonical TOML once per definition.
  useEffect(() => {
    load();
  }, [load]);

  const toml = editedToml ?? (exported?.ok ? (exported.toml ?? "") : "");
  const loading = editedToml == null && !exported && manifest.loading;
  const exportFailed =
    editedToml == null &&
    ((exported != null && !exported.ok) || (exported == null && manifest.error != null));
  const ready = !loading && !exportFailed;

  const setToml = (value: string) => setEditedToml(value);

  const handleValidate = async (source?: string) => {
    setValidating(true);
    try {
      setPreview(await manifest.preview(source ?? toml));
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
      const invalid = await manifest.importAsNew(toml);
      if (invalid) setPreview(invalid);
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
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => fileRef.current?.click()}
          >
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
          <p>{exported?.error ?? manifest.error ?? "Failed to export the manifest."}</p>
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
                Valid: <span className="font-medium">{preview.definition.name}</span> (
                {preview.definition.pattern}), {preview.stages.length} stage
                {preview.stages.length === 1 ? "" : "s"}.
              </p>
            </div>
          )}
          <p className="text-muted-foreground text-xs">
            Applying code edits to this definition in place isn&apos;t supported yet: TOML stages
            carry no stable identity, so edits can&apos;t be mapped safely onto existing stages.
            Export, edit, and use &quot;Import as new&quot; to create a definition from the edited
            manifest.
          </p>
        </div>
      )}
    </Section>
  );
}

// ─── Main builder ────────────────────────────────────────────────────────

type BuilderView = "stages" | "code";

const VIEW_OPTIONS: {
  value: BuilderView;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
}[] = [
  { value: "stages", label: "Stages", icon: ListIcon },
  { value: "code", label: "Code", icon: CodeIcon },
];

export type StageBuilderProps = ReturnType<typeof useStageBuilder>;

export function StageBuilder({
  slug,
  orgId,
  canCreate,
  canManage,
  definition,
  defLoading,
  stages: sorted,
  stagesLoading,
  busyGuid,
  creating,
  cloning,
  onSaveStage: handleSaveStage,
  onDeleteStage: handleDeleteStage,
  onMoveStage: handleMoveStage,
  onCreateStage,
  onClone: handleClone,
  manifest,
  pickerOptions,
}: StageBuilderProps) {
  const [view, setView] = useState<BuilderView>("stages");
  // One open card at a time keeps the tab within about two screens.
  const [openGuid, setOpenGuid] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);

  const isGlobal =
    definition != null && (definition.isGlobal || definition.organizationGuid == null);
  const isSourceManaged = Boolean(definition?.sourceRepo);
  const readOnly = isGlobal || isSourceManaged || !canManage;

  const toggleExpanded = useCallback((guid: string) => {
    setOpenGuid((prev) => (prev === guid ? null : guid));
  }, []);

  const handleCreateStage = async (draft: StageDraft) => {
    if (await onCreateStage(draft)) setAdding(false);
  };

  // The definition as one line of the workflow views: stations in stage
  // order, fan-out and its join, supervisors, nested workflows and retries.
  const line = useMemo(
    () =>
      definition
        ? definitionLine(
            definition,
            sorted.map((stage) => ({ ...stage, agentName: stage.agentDefinitionName }))
          )
        : null,
    [definition, sorted]
  );

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
    <StageTargetsContext.Provider value={sorted}>
      <PickerOptionsContext.Provider value={pickerOptions}>
        <PageShell
          title={`Builder · ${definition.name}`}
          description={
            definition.description || `Ordered stage pipeline (${definition.patternKind}).`
          }
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
                  <span className="font-medium">Platform template</span>, read-only. Clone it to
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
                <span className="font-medium">Repository managed</span>: edit{" "}
                <code>
                  {definition.sourceRepo}/{definition.sourcePath}
                </code>{" "}
                and sync the agent repository.
                {definition.sourceRef
                  ? ` Last reconciled at ${definition.sourceRef.slice(0, 12)}.`
                  : ""}
              </span>
            </div>
          )}

          {view === "code" ? (
            <CodeView slug={slug} canCreate={canCreate} manifest={manifest} />
          ) : (
            <Section
              title="Stages"
              description="An ordered pipeline: stages run in sequence; fan-out runs parallel copies merged by a later aggregation stage."
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
              ) : (
                <div className="grid min-w-0 gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
                  {line && (
                    <WorkflowView
                      snapshot={definitionSnapshot(line)}
                      title="Shape"
                      description={`${lineShape(line)}. Pick a station to open its stage.`}
                      onSelectStation={(_, stationId) => setOpenGuid(stationId)}
                      className="min-w-0 self-start xl:sticky xl:top-4"
                    />
                  )}
                  <ol className="flex min-w-0 flex-col gap-3" aria-label="Stages">
                    {sorted.map((stage, index) => (
                      <li key={stage.guid} className="min-w-0">
                        <StageCard
                          stage={stage}
                          index={index}
                          total={sorted.length}
                          orgScoped={orgId}
                          readOnly={readOnly}
                          expanded={openGuid === stage.guid}
                          saving={busyGuid === stage.guid}
                          onToggle={toggleExpanded}
                          onSave={handleSaveStage}
                          onDelete={handleDeleteStage}
                          onMove={handleMoveStage}
                        />
                      </li>
                    ))}
                  </ol>
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
      </PickerOptionsContext.Provider>
    </StageTargetsContext.Provider>
  );
}
