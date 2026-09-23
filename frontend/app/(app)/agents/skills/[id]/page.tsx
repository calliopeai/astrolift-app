"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { useMutation, useQuery } from "@apollo/client/react";
import {
  ChevronRightIcon,
  CodeIcon,
  Loader2Icon,
  PlusIcon,
  SparklesIcon,
  TrashIcon,
  WrenchIcon,
} from "lucide-react";
import { toast } from "sonner";
import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { EmptyState } from "@/components/EmptyState";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PageShell } from "@/components/PageShell";
import { Separator } from "@/components/ui/separator";
import { Textarea } from "@/components/ui/textarea";
import {
  DELETE_SKILL,
  UPDATE_SKILL,
  CREATE_TOOL_DEF,
  DELETE_TOOL_DEF,
} from "@/graphql/agents/agents.mutations";
import { GET_SKILL, LIST_TOOL_DEFS } from "@/graphql/agents/agents.queries";

// ─── Mutation response types ──────────────────────────────────────────────────

type MutError = { field: string; message: string; code: string };

type UpdateSkillData = {
  updateSkill: { ok: boolean; errors: MutError[]; data: { id: string; slug: string } | null };
};

type DeleteSkillData = {
  deleteSkill: { ok: boolean; errors: MutError[] };
};

type CreateToolDefData = {
  createToolDef: { ok: boolean; errors: MutError[]; data: { id: string } | null };
};

type DeleteToolDefData = {
  deleteToolDef: { ok: boolean; errors: MutError[] };
};

// ─── Types ────────────────────────────────────────────────────────────────────

type Adapter = "python_fn" | "http_endpoint" | "mcp_server";

const ADAPTERS: { value: Adapter; label: string }[] = [
  { value: "python_fn", label: "Python function" },
  { value: "http_endpoint", label: "HTTP endpoint" },
  { value: "mcp_server", label: "MCP server" },
];

type Skill = {
  id: string;
  name: string;
  slug: string;
  description: string;
  content: string;
  skillVersion: number;
  isGlobal: boolean;
  isActive: boolean;
};

type ToolDef = {
  id: string;
  name: string;
  slug: string;
  description: string;
  adapter: string;
  handlerRef: string;
  inputSchema: unknown;
  outputSchema: unknown;
  createdAt: string;
};

type SkillData = { skill: Skill | null };
type ToolDefsData = { toolDefs: ToolDef[] };

// ─── Add tool form ────────────────────────────────────────────────────────────

function AddToolForm({
  skillId,
  onDone,
}: {
  skillId: string;
  onDone: () => void;
}) {
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [description, setDescription] = useState("");
  const [adapter, setAdapter] = useState<Adapter>("python_fn");
  const [handlerRef, setHandlerRef] = useState("");
  const [inputSchemaText, setInputSchemaText] = useState("{}");
  const [outputSchemaText, setOutputSchemaText] = useState("{}");

  const [createToolDef, { loading }] = useMutation<CreateToolDefData>(CREATE_TOOL_DEF, {
    refetchQueries: ["ListToolDefs"],
  });

  function autoSlug(value: string) {
    setSlug(
      value
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "_")
        .replace(/^_|_$/g, "")
    );
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim() || !slug.trim()) {
      toast.error("Name and slug are required");
      return;
    }
    let parsedInput: unknown = {};
    let parsedOutput: unknown = {};
    try {
      parsedInput = JSON.parse(inputSchemaText);
    } catch {
      toast.error("Input schema is not valid JSON");
      return;
    }
    try {
      parsedOutput = JSON.parse(outputSchemaText);
    } catch {
      toast.error("Output schema is not valid JSON");
      return;
    }
    const { data } = await createToolDef({
      variables: {
        skillId,
        input: {
          name: name.trim(),
          slug: slug.trim(),
          description: description.trim(),
          adapter,
          handlerRef: handlerRef.trim(),
          inputSchema: parsedInput,
          outputSchema: parsedOutput,
          implementationConfig: null,
        },
      },
    });
    if (data?.createToolDef?.ok) {
      toast.success("Tool registered");
      onDone();
    } else {
      for (const err of data?.createToolDef?.errors ?? []) {
        toast.error(`${err.field}: ${err.message}`);
      }
    }
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="flex flex-col gap-4 rounded-lg border p-4"
    >
      <p className="text-sm font-semibold">Register tool definition</p>

      <div className="grid gap-3 sm:grid-cols-2">
        <div className="flex flex-col gap-1">
          <Label className="text-xs">Name</Label>
          <Input
            value={name}
            placeholder="e.g. search_docs"
            className="h-8 text-sm"
            onChange={(e) => {
              setName(e.target.value);
              autoSlug(e.target.value);
            }}
          />
        </div>
        <div className="flex flex-col gap-1">
          <Label className="text-xs">Slug</Label>
          <Input
            value={slug}
            placeholder="search_docs"
            className="h-8 font-mono text-sm"
            onChange={(e) => setSlug(e.target.value)}
          />
        </div>
      </div>

      <div className="flex flex-col gap-1">
        <Label className="text-xs">Description</Label>
        <Input
          value={description}
          placeholder="What this tool does"
          className="h-8 text-sm"
          onChange={(e) => setDescription(e.target.value)}
        />
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <div className="flex flex-col gap-1">
          <Label className="text-xs">Adapter</Label>
          <select
            value={adapter}
            onChange={(e) => setAdapter(e.target.value as Adapter)}
            className="border-input bg-background h-8 rounded-md border px-2 text-sm"
          >
            {ADAPTERS.map((a) => (
              <option key={a.value} value={a.value}>
                {a.label}
              </option>
            ))}
          </select>
        </div>
        <div className="flex flex-col gap-1">
          <Label className="text-xs">Handler ref</Label>
          <Input
            value={handlerRef}
            placeholder="module.function or https://..."
            className="h-8 font-mono text-sm"
            onChange={(e) => setHandlerRef(e.target.value)}
          />
        </div>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <div className="flex flex-col gap-1">
          <Label className="text-xs">Input schema (JSON)</Label>
          <Textarea
            value={inputSchemaText}
            rows={3}
            className="font-mono text-xs"
            onChange={(e) => setInputSchemaText(e.target.value)}
          />
        </div>
        <div className="flex flex-col gap-1">
          <Label className="text-xs">Output schema (JSON)</Label>
          <Textarea
            value={outputSchemaText}
            rows={3}
            className="font-mono text-xs"
            onChange={(e) => setOutputSchemaText(e.target.value)}
          />
        </div>
      </div>

      <div className="flex gap-2">
        <Button type="submit" size="sm" disabled={loading}>
          {loading ? (
            <Loader2Icon className="mr-1 h-3.5 w-3.5 animate-spin" />
          ) : (
            <PlusIcon className="mr-1 h-3.5 w-3.5" />
          )}
          Register tool
        </Button>
        <Button type="button" variant="ghost" size="sm" onClick={onDone}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function SkillBuilderPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();

  // ── form state ──────────────────────────────────────────────────────────────
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [description, setDescription] = useState("");
  const [content, setContent] = useState("");
  const [showAddTool, setShowAddTool] = useState(false);
  const [aiAssisting, setAiAssisting] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [confirmDeleteSkill, setConfirmDeleteSkill] = useState(false);
  const [toolToRemove, setToolToRemove] = useState<ToolDef | null>(null);

  // ── data ────────────────────────────────────────────────────────────────────
  const { data: skillData, loading: skillLoading, error: skillError } = useQuery<SkillData>(
    GET_SKILL,
    { variables: { id }, fetchPolicy: "cache-and-network", skip: !id }
  );

  const { data: toolsData, loading: toolsLoading } = useQuery<ToolDefsData>(
    LIST_TOOL_DEFS,
    { variables: { skillId: id }, fetchPolicy: "cache-and-network", skip: !id }
  );

  const [updateSkill, { loading: saving }] = useMutation<UpdateSkillData>(UPDATE_SKILL, {
    refetchQueries: ["GetSkill"],
  });

  const [deleteSkill, { loading: deleting }] = useMutation<DeleteSkillData>(DELETE_SKILL, {
    refetchQueries: ["ListSkills"],
  });

  const [deleteToolDef, { loading: deletingTool }] = useMutation<DeleteToolDefData>(DELETE_TOOL_DEF, {
    refetchQueries: ["ListToolDefs"],
  });

  const skill = skillData?.skill ?? null;
  const tools = toolsData?.toolDefs ?? [];

  // Seed form when skill loads
  useEffect(() => {
    if (skill && !dirty) {
      setName(skill.name);
      setSlug(skill.slug);
      setDescription(skill.description);
      setContent(skill.content);
    }
  }, [skill, dirty]);

  // ── handlers ────────────────────────────────────────────────────────────────

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim() || !slug.trim()) {
      toast.error("Name and slug are required");
      return;
    }
    const { data } = await updateSkill({
      variables: {
        id,
        input: {
          name: name.trim(),
          slug: slug.trim(),
          description: description.trim(),
          content: content.trim(),
          dependencies: null,
        },
      },
    });
    if (data?.updateSkill?.ok) {
      toast.success("Skill saved");
      setDirty(false);
    } else {
      for (const err of data?.updateSkill?.errors ?? []) {
        toast.error(`${err.field}: ${err.message}`);
      }
    }
  }

  // Both destructive handlers throw on failure: ConfirmDialog keeps the
  // dialog open and surfaces the message as a toast, so a failed delete
  // stays correctable instead of dismissing itself.
  async function handleDelete() {
    const { data } = await deleteSkill({ variables: { id } });
    if (data?.deleteSkill?.ok) {
      toast.success("Skill deleted");
      router.push("/agents/skills");
    } else {
      throw new Error("Failed to delete skill");
    }
  }

  async function handleDeleteTool(toolId: string) {
    const { data } = await deleteToolDef({ variables: { id: toolId } });
    if (!data?.deleteToolDef?.ok) {
      throw new Error("Failed to remove tool");
    }
  }

  async function handleAiAssist() {
    if (!description.trim() && !name.trim()) {
      toast.error("Add a name or description first so the AI has context");
      return;
    }
    setAiAssisting(true);
    try {
      const res = await fetch("/api/agents/v1/skills/ai-assist/", {
        method: "POST",
        headers: { "Content-Type": "application/json", "x-platform": "web" },
        body: JSON.stringify({ name: name.trim(), description: description.trim(), content }),
      });
      if (!res.ok) {
        const body = await res.text();
        throw new Error(body || `HTTP ${res.status}`);
      }
      const json = await res.json();
      if (json.content) {
        setContent(json.content);
        setDirty(true);
        toast.success("AI-generated content applied — review before saving");
      }
    } catch (err) {
      toast.error(`AI assist failed: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setAiAssisting(false);
    }
  }

  // ── loading / error states ───────────────────────────────────────────────────

  if (skillLoading && !skill) {
    return (
      <div className="flex flex-1 items-center justify-center p-6">
        <Loader2Icon className="text-muted-foreground h-6 w-6 animate-spin" />
      </div>
    );
  }

  if (skillError || !skill) {
    return (
      <PageShell title="Skill not found">
        <EmptyState
          icon={<SparklesIcon className="size-5" />}
          title={skillError ? "Couldn't load this skill" : "Skill not found"}
          description={
            skillError
              ? skillError.message
              : "This skill may have been deleted, or you may not have access to it."
          }
          actionHref="/agents/skills"
          actionLabel="Back to skills"
        />
      </PageShell>
    );
  }

  return (
    <PageShell
      title={
        <span className="flex items-center gap-2">
          <Link href="/agents/skills" className="text-muted-foreground hover:text-foreground">
            Skills
          </Link>
          <ChevronRightIcon className="text-muted-foreground h-4 w-4" />
          {skill.name}
        </span>
      }
      description={
        <span className="flex items-center gap-2">
          <span className="font-mono text-xs">{skill.slug}</span>
          <Badge variant={skill.isActive ? "default" : "secondary"} className="text-xs">
            {skill.isActive ? "Active" : "Inactive"}
          </Badge>
          {skill.isGlobal && (
            <Badge variant="outline" className="text-xs">
              Global
            </Badge>
          )}
          <span className="text-muted-foreground text-xs">v{skill.skillVersion}</span>
        </span>
      }
      actions={
        <Button
          variant="ghost"
          size="sm"
          onClick={() => setConfirmDeleteSkill(true)}
          disabled={deleting}
          className="text-destructive hover:text-destructive"
        >
          {deleting ? (
            <Loader2Icon className="mr-1 h-3.5 w-3.5 animate-spin" />
          ) : (
            <TrashIcon className="mr-1 h-3.5 w-3.5" />
          )}
          Delete
        </Button>
      }
    >
      {/* ── Skill form ──────────────────────────────────────────────────────── */}
      <form onSubmit={handleSave} className="flex flex-col gap-6">
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="flex flex-col gap-1.5">
            <Label>Name</Label>
            <Input
              value={name}
              onChange={(e) => { setName(e.target.value); setDirty(true); }}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label>Slug</Label>
            <Input
              value={slug}
              className="font-mono"
              onChange={(e) => { setSlug(e.target.value); setDirty(true); }}
            />
          </div>
        </div>

        <div className="flex flex-col gap-1.5">
          <Label>Description</Label>
          <Input
            value={description}
            placeholder="Short description shown in search and picker"
            onChange={(e) => { setDescription(e.target.value); setDirty(true); }}
          />
        </div>

        <Separator />

        <div className="flex flex-col gap-1.5">
          <div className="flex items-center justify-between">
            <Label>Instructions / content</Label>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={handleAiAssist}
              disabled={aiAssisting}
            >
              {aiAssisting ? (
                <Loader2Icon className="mr-1 h-3.5 w-3.5 animate-spin" />
              ) : (
                <SparklesIcon className="mr-1 h-3.5 w-3.5" />
              )}
              AI assist
            </Button>
          </div>
          <Textarea
            value={content}
            placeholder="System prompt / instructions injected into the agent brief when this skill is active"
            rows={10}
            className="font-mono text-sm"
            onChange={(e) => { setContent(e.target.value); setDirty(true); }}
          />
        </div>

        <div className="flex items-center gap-2">
          <Button type="submit" disabled={saving || !dirty} size="sm">
            {saving ? (
              <Loader2Icon className="mr-1 h-3.5 w-3.5 animate-spin" />
            ) : null}
            Save skill
          </Button>
          {dirty && (
            <span className="text-muted-foreground text-xs">Unsaved changes</span>
          )}
        </div>
      </form>

      <Separator />

      {/* ── Tool definitions ────────────────────────────────────────────────── */}
      <section className="flex flex-col gap-3">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold">
            Tool definitions
            <Badge variant="outline" className="ml-2 text-xs">
              {tools.length}
            </Badge>
          </h2>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setShowAddTool((v) => !v)}
          >
            <PlusIcon className="mr-1 h-3.5 w-3.5" />
            {showAddTool ? "Cancel" : "Register tool"}
          </Button>
        </div>

        {showAddTool && (
          <AddToolForm skillId={id} onDone={() => setShowAddTool(false)} />
        )}

        {toolsLoading && tools.length === 0 && (
          <Loader2Icon className="text-muted-foreground h-4 w-4 animate-spin" />
        )}

        {!toolsLoading && tools.length === 0 && !showAddTool && (
          <EmptyState
            icon={<CodeIcon className="size-5" />}
            title="No tool definitions"
            description="Register tool definitions to give this skill executable capabilities."
            secondary={
              <Button size="sm" onClick={() => setShowAddTool(true)}>
                <PlusIcon className="mr-1 h-3.5 w-3.5" /> Register first tool
              </Button>
            }
          />
        )}

        {tools.length > 0 && (
          <div className="flex flex-col gap-2">
            {tools.map((tool) => (
              <div
                key={tool.id}
                className="flex items-center gap-4 rounded-md border px-4 py-3 text-sm"
              >
                <WrenchIcon className="text-muted-foreground h-4 w-4 shrink-0" />
                <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                  <span className="font-medium">{tool.name}</span>
                  {tool.description && (
                    <span className="text-muted-foreground truncate text-xs">
                      {tool.description}
                    </span>
                  )}
                </div>
                <Badge variant="secondary" className="shrink-0 text-xs">
                  {ADAPTERS.find((a) => a.value === tool.adapter)?.label ?? tool.adapter}
                </Badge>
                {tool.handlerRef && (
                  <span className="text-muted-foreground max-w-[180px] truncate font-mono text-xs">
                    {tool.handlerRef}
                  </span>
                )}
                <Link
                  href={`/agents/tools/${tool.id}`}
                  className="text-muted-foreground hover:text-foreground shrink-0 text-xs underline-offset-2 hover:underline"
                >
                  Edit
                </Link>
                <button
                  type="button"
                  onClick={() => setToolToRemove(tool)}
                  disabled={deletingTool}
                  className="text-muted-foreground hover:text-destructive ml-1 shrink-0"
                  aria-label={`Remove ${tool.name}`}
                >
                  <TrashIcon className="h-3.5 w-3.5" />
                </button>
              </div>
            ))}
          </div>
        )}
      </section>

      <ConfirmDialog
        open={confirmDeleteSkill}
        onOpenChange={setConfirmDeleteSkill}
        title={`Delete skill "${skill.name}"?`}
        description="This cannot be undone. Agents that reference this skill lose it on their next run, and its tool definitions go with it."
        confirmLabel="Delete skill"
        destructive
        onConfirm={handleDelete}
      />

      <ConfirmDialog
        open={toolToRemove !== null}
        onOpenChange={(next) => {
          if (!next) setToolToRemove(null);
        }}
        title={toolToRemove ? `Remove tool "${toolToRemove.name}"?` : "Remove tool?"}
        description="The tool definition is deleted from this skill. Agents lose the capability on their next run."
        confirmLabel="Remove tool"
        destructive
        onConfirm={async () => {
          if (toolToRemove) await handleDeleteTool(toolToRemove.id);
        }}
      />
    </PageShell>
  );
}
