"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { gql } from "@apollo/client";
import { useMutation, useQuery } from "@apollo/client/react";
import {
  ChevronRightIcon,
  Loader2Icon,
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
  DELETE_TOOL_DEF,
  UPDATE_TOOL_DEF,
} from "@/graphql/agents/agents.mutations";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";

// ─── GraphQL ─────────────────────────────────────────────────────────────────

// The global toolDefs resolver is skill-scoped, so we fetch the single
// tool def via the list query on its parent skill — but we don't have
// the parent skill from the URL here. Use a local inline query on the
// orgToolDefs resolver instead, filtering client-side by ID.
// Alternatively, use a dedicated GET_TOOL_DEF query once it exists.
// For now we re-use LIST_ORG_TOOL_DEFS and find by id param.

const GET_TOOL_DEF = gql`
  query GetToolDef($orgId: ID!, $id: ID!) {
    orgToolDefs(orgId: $orgId) {
      id
      name
      slug
      description
      adapter
      inputSchema
      outputSchema
      handlerRef
      createdAt
    }
  }
`;

// ─── Types ────────────────────────────────────────────────────────────────────

type Adapter = "python_fn" | "http_endpoint" | "mcp_server";

const ADAPTERS: { value: Adapter; label: string }[] = [
  { value: "python_fn", label: "Python function" },
  { value: "http_endpoint", label: "HTTP endpoint" },
  { value: "mcp_server", label: "MCP server" },
];

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

type OrgToolDefsData = { orgToolDefs: ToolDef[] };

// ─── Mutation response types ──────────────────────────────────────────────────

type MutError = { field: string; message: string; code: string };

type UpdateToolDefData = {
  updateToolDef: {
    ok: boolean;
    errors: MutError[];
    data: { id: string; name: string; slug: string; adapter: string } | null;
  };
};

type DeleteToolDefData = {
  deleteToolDef: { ok: boolean; errors: MutError[] };
};

// ─── Helpers ─────────────────────────────────────────────────────────────────

function prettyJson(v: unknown): string {
  try {
    return JSON.stringify(v, null, 2);
  } catch {
    return "{}";
  }
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function ToolDetailPage() {
  // The dynamic segment is named [slug] but we use it as the tool GUID.
  const { slug: id } = useParams<{ slug: string }>();
  const router = useRouter();
  // Reactive org id (#1022): the synchronous cookie read races the
  // post-render effect that sets it, leaving orgId "" on cold load.
  const { org } = useActiveOrg();
  const orgId = org?.id ?? "";

  // ── form state ──────────────────────────────────────────────────────────────
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [description, setDescription] = useState("");
  const [adapter, setAdapter] = useState<Adapter>("python_fn");
  const [handlerRef, setHandlerRef] = useState("");
  const [inputSchemaText, setInputSchemaText] = useState("{}");
  const [outputSchemaText, setOutputSchemaText] = useState("{}");
  const [schemaError, setSchemaError] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const { data, loading, error } = useQuery<OrgToolDefsData>(GET_TOOL_DEF, {
    variables: { orgId, id },
    fetchPolicy: "cache-and-network",
    skip: !orgId,
  });

  const tool = data?.orgToolDefs?.find((t) => t.id === id) ?? null;

  const [updateToolDef, { loading: saving }] = useMutation<UpdateToolDefData>(UPDATE_TOOL_DEF, {
    refetchQueries: ["ListOrgToolDefs", "ListToolDefs"],
  });

  const [deleteToolDef, { loading: deleting }] = useMutation<DeleteToolDefData>(DELETE_TOOL_DEF, {
    refetchQueries: ["ListOrgToolDefs", "ListToolDefs"],
  });

  // Seed form when tool loads
  useEffect(() => {
    if (tool && !dirty) {
      setName(tool.name);
      setSlug(tool.slug);
      setDescription(tool.description);
      setAdapter((tool.adapter as Adapter) || "python_fn");
      setHandlerRef(tool.handlerRef);
      setInputSchemaText(prettyJson(tool.inputSchema));
      setOutputSchemaText(prettyJson(tool.outputSchema));
    }
  }, [tool, dirty]);

  // ── handlers ────────────────────────────────────────────────────────────────

  async function handleSave(e: React.FormEvent) {
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
      setSchemaError("Input schema is not valid JSON");
      return;
    }
    try {
      parsedOutput = JSON.parse(outputSchemaText);
    } catch {
      setSchemaError("Output schema is not valid JSON");
      return;
    }
    setSchemaError(null);
    const { data: mutData } = await updateToolDef({
      variables: {
        id,
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
    if (mutData?.updateToolDef?.ok) {
      toast.success("Tool saved");
      setDirty(false);
    } else {
      for (const err of mutData?.updateToolDef?.errors ?? []) {
        toast.error(`${err.field}: ${err.message}`);
      }
    }
  }

  // Throws on failure so ConfirmDialog holds the dialog open and toasts
  // the message, instead of closing on a delete that never happened.
  async function handleDelete() {
    const { data: mutData } = await deleteToolDef({ variables: { id } });
    if (mutData?.deleteToolDef?.ok) {
      toast.success("Tool deleted");
      router.push("/agents/tools");
    } else {
      throw new Error("Failed to delete tool");
    }
  }

  // ── loading / error states ───────────────────────────────────────────────────

  if (loading && !tool) {
    return (
      <div className="flex flex-1 items-center justify-center p-6">
        <Loader2Icon className="text-muted-foreground h-6 w-6 animate-spin" />
      </div>
    );
  }

  if (error || !tool) {
    return (
      <PageShell title="Tool not found">
        <EmptyState
          icon={<WrenchIcon className="size-5" />}
          title={error ? "Couldn't load this tool" : "Tool not found"}
          description={
            error
              ? error.message
              : "This tool may have been deleted, or you may not have access to it."
          }
          actionHref="/agents/tools"
          actionLabel="Back to tools"
        />
      </PageShell>
    );
  }

  return (
    <PageShell
      title={
        <span className="flex items-center gap-2">
          <Link href="/agents/tools" className="text-muted-foreground hover:text-foreground">
            Tools
          </Link>
          <ChevronRightIcon className="text-muted-foreground h-4 w-4" />
          {tool.name}
        </span>
      }
      description={
        <span className="flex items-center gap-2">
          <span className="font-mono text-xs">{tool.slug}</span>
          <Badge variant="secondary" className="text-xs">
            {ADAPTERS.find((a) => a.value === tool.adapter)?.label ?? tool.adapter}
          </Badge>
        </span>
      }
      actions={
        <Button
          variant="ghost"
          size="sm"
          onClick={() => setConfirmDelete(true)}
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
      <form onSubmit={handleSave} className="flex flex-col gap-6">
        {/* Identity */}
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
            placeholder="What this tool does"
            onChange={(e) => { setDescription(e.target.value); setDirty(true); }}
          />
        </div>

        <Separator />

        {/* Adapter + handler */}
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="flex flex-col gap-1.5">
            <Label>Adapter</Label>
            <select
              value={adapter}
              onChange={(e) => { setAdapter(e.target.value as Adapter); setDirty(true); }}
              className="border-input bg-background h-9 rounded-md border px-2 text-sm"
            >
              {ADAPTERS.map((a) => (
                <option key={a.value} value={a.value}>
                  {a.label}
                </option>
              ))}
            </select>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label>Handler ref</Label>
            <Input
              value={handlerRef}
              placeholder={
                adapter === "python_fn"
                  ? "module.function"
                  : adapter === "http_endpoint"
                  ? "https://example.com/tool"
                  : "mcp://server-address"
              }
              className="font-mono"
              onChange={(e) => { setHandlerRef(e.target.value); setDirty(true); }}
            />
          </div>
        </div>

        <Separator />

        {/* JSON schema editors */}
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="flex flex-col gap-1.5">
            <Label>Input schema (JSON Schema)</Label>
            <Textarea
              value={inputSchemaText}
              rows={8}
              className="font-mono text-xs"
              onChange={(e) => { setInputSchemaText(e.target.value); setDirty(true); }}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label>Output schema (JSON Schema)</Label>
            <Textarea
              value={outputSchemaText}
              rows={8}
              className="font-mono text-xs"
              onChange={(e) => { setOutputSchemaText(e.target.value); setDirty(true); }}
            />
          </div>
        </div>

        {schemaError && (
          <p className="text-destructive text-sm">{schemaError}</p>
        )}

        <div className="flex items-center gap-2">
          <Button type="submit" size="sm" disabled={saving || !dirty}>
            {saving ? (
              <Loader2Icon className="mr-1 h-3.5 w-3.5 animate-spin" />
            ) : null}
            Save tool
          </Button>
          {dirty && (
            <span className="text-muted-foreground text-xs">Unsaved changes</span>
          )}
        </div>
      </form>

      <ConfirmDialog
        open={confirmDelete}
        onOpenChange={setConfirmDelete}
        title={`Delete tool "${tool.name}"?`}
        description="This cannot be undone. Skills and agents that call this tool lose the capability on their next run."
        confirmLabel="Delete tool"
        destructive
        onConfirm={handleDelete}
      />
    </PageShell>
  );
}
