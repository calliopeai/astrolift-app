"use client";

import { useState } from "react";
import { useParams } from "next/navigation";
import { gql } from "@apollo/client";
import { useMutation, useQuery } from "@apollo/client/react";
import {
  ChevronRightIcon,
  CodeIcon,
  Loader2Icon,
  PlusIcon,
  WrenchIcon,
} from "lucide-react";
import { toast } from "sonner";
import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/EmptyState";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PageShell } from "@/components/PageShell";
import { Separator } from "@/components/ui/separator";

// ─── Types ────────────────────────────────────────────────────────────────────

type ImplementationKind =
  | "python_fn"
  | "http_endpoint"
  | "mcp_server";

type ToolDef = {
  id: string;
  name: string;
  implementationKind: ImplementationKind;
  handlerRef: string | null;
};

type Skill = {
  id: string;
  name: string;
  slug: string;
  description: string | null;
  version: string;
  isActive: boolean;
  tools: ToolDef[];
};

type SkillData = {
  agentSkill: Skill | null;
};

type AddToolResult = {
  addSkillToolDef: {
    ok: boolean;
    errors: { field: string; messages: string[] }[];
  };
};

// ─── GraphQL ─────────────────────────────────────────────────────────────────

const GET_SKILL = gql`
  query GetAgentSkill($id: ID!) {
    agentSkill(id: $id) {
      id
      name
      slug
      description
      version
      isActive
      tools {
        id
        name
        implementationKind
        handlerRef
      }
    }
  }
`;

const ADD_TOOL_DEF = gql`
  mutation AddSkillToolDef(
    $skillId: ID!
    $name: String!
    $implementationKind: String!
    $handlerRef: String
  ) {
    addSkillToolDef(
      skillId: $skillId
      name: $name
      implementationKind: $implementationKind
      handlerRef: $handlerRef
    ) {
      ok
      errors {
        field
        messages
      }
    }
  }
`;

// ─── Constants ────────────────────────────────────────────────────────────────

const IMPL_KINDS: { value: ImplementationKind; label: string }[] = [
  { value: "python_fn", label: "Python function" },
  { value: "http_endpoint", label: "HTTP endpoint" },
  { value: "mcp_server", label: "MCP server" },
];

// ─── Sub-components ───────────────────────────────────────────────────────────

function AddToolForm({
  skillId,
  onAdded,
}: {
  skillId: string;
  onAdded: () => void;
}) {
  const [name, setName] = useState("");
  const [implementationKind, setImplementationKind] =
    useState<ImplementationKind>("python_fn");
  const [handlerRef, setHandlerRef] = useState("");

  const [addToolDef, { loading }] = useMutation<AddToolResult>(ADD_TOOL_DEF, {
    refetchQueries: [GET_SKILL],
  });

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) {
      toast.error("Tool name is required");
      return;
    }
    const { data } = await addToolDef({
      variables: {
        skillId,
        name: name.trim(),
        implementationKind,
        handlerRef: handlerRef.trim() || undefined,
      },
    });
    if (data?.addSkillToolDef?.ok) {
      toast.success("Tool added");
      setName("");
      setHandlerRef("");
      onAdded();
    } else {
      for (const e of data?.addSkillToolDef?.errors ?? []) {
        toast.error(`${e.field}: ${e.messages.join(", ")}`);
      }
    }
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-4 rounded-lg border p-4">
      <p className="text-sm font-medium">Add tool definition</p>
      <div className="grid gap-3 sm:grid-cols-3">
        <div className="flex flex-col gap-1">
          <Label className="text-xs">Name</Label>
          <Input
            value={name}
            placeholder="e.g. search_docs"
            className="h-8 text-sm"
            onChange={(e) => setName(e.target.value)}
          />
        </div>
        <div className="flex flex-col gap-1">
          <Label className="text-xs">Implementation kind</Label>
          <select
            value={implementationKind}
            onChange={(e) =>
              setImplementationKind(e.target.value as ImplementationKind)
            }
            className="border-input bg-background h-8 rounded-md border px-2 text-sm"
          >
            {IMPL_KINDS.map((k) => (
              <option key={k.value} value={k.value}>
                {k.label}
              </option>
            ))}
          </select>
        </div>
        <div className="flex flex-col gap-1">
          <Label className="text-xs">Handler ref</Label>
          <Input
            value={handlerRef}
            placeholder="module.function or https://..."
            className="h-8 text-sm"
            onChange={(e) => setHandlerRef(e.target.value)}
          />
        </div>
      </div>
      <Button type="submit" size="sm" disabled={loading} className="self-start">
        {loading ? (
          <Loader2Icon className="mr-1 h-3.5 w-3.5 animate-spin" />
        ) : (
          <PlusIcon className="mr-1 h-3.5 w-3.5" />
        )}
        Add tool
      </Button>
    </form>
  );
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function SkillDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [showAddTool, setShowAddTool] = useState(false);

  const { data, loading, error, refetch } = useQuery<SkillData>(GET_SKILL, {
    variables: { id },
    fetchPolicy: "cache-and-network",
    skip: !id,
  });

  const skill = data?.agentSkill ?? null;

  if (loading && !skill) {
    return (
      <div className="flex flex-1 items-center justify-center p-6">
        <Loader2Icon className="text-muted-foreground h-6 w-6 animate-spin" />
      </div>
    );
  }

  if (error || !skill) {
    return (
      <div className="flex flex-1 flex-col gap-6 p-6">
        <div className="text-destructive bg-destructive/10 border-destructive/20 rounded-md border p-4 text-sm">
          {error ? `Error: ${error.message}` : `Skill not found`}
        </div>
      </div>
    );
  }

  return (
    <PageShell
      title={
        <span className="flex items-center gap-2">
          <Link href="/skills" className="text-muted-foreground hover:text-foreground">
            Skills
          </Link>
          <ChevronRightIcon className="text-muted-foreground h-4 w-4" />
          {skill.name}
        </span>
      }
      description={skill.description ?? undefined}
      actions={
        <Button variant="outline" size="sm" onClick={() => setShowAddTool((v) => !v)}>
          <PlusIcon className="mr-1 h-3.5 w-3.5" />
          {showAddTool ? "Cancel" : "Add tool"}
        </Button>
      }
    >
      {/* Metadata */}
      <section className="flex flex-col gap-3">
        <h2 className="text-sm font-semibold">Metadata</h2>
        <div className="grid gap-3 sm:grid-cols-3 text-sm">
          <div className="flex flex-col gap-0.5">
            <span className="text-muted-foreground text-xs">Slug</span>
            <span className="font-mono">{skill.slug}</span>
          </div>
          <div className="flex flex-col gap-0.5">
            <span className="text-muted-foreground text-xs">Version</span>
            <span>{skill.version}</span>
          </div>
          <div className="flex flex-col gap-0.5">
            <span className="text-muted-foreground text-xs">Status</span>
            <Badge
              variant={skill.isActive ? "default" : "secondary"}
              className="w-fit text-xs"
            >
              {skill.isActive ? "Active" : "Inactive"}
            </Badge>
          </div>
        </div>
      </section>

      <Separator />

      {/* Add tool form */}
      {showAddTool && (
        <AddToolForm skillId={skill.id} onAdded={() => setShowAddTool(false)} />
      )}

      {/* Tool definitions */}
      <section className="flex flex-col gap-3">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold">
            Tool definitions
            <Badge variant="outline" className="ml-2 text-xs">
              {skill.tools.length}
            </Badge>
          </h2>
        </div>

        {skill.tools.length === 0 ? (
          <EmptyState
            icon={<CodeIcon className="size-5" />}
            title="No tools defined"
            description="Add tool definitions to give this skill executable capabilities."
            secondary={
              <Button
                size="sm"
                onClick={() => setShowAddTool(true)}
              >
                <PlusIcon className="mr-1 h-3.5 w-3.5" /> Add first tool
              </Button>
            }
          />
        ) : (
          <div className="flex flex-col gap-2">
            {skill.tools.map((tool) => (
              <div
                key={tool.id}
                className="flex items-center gap-4 rounded-md border px-4 py-3 text-sm"
              >
                <WrenchIcon className="text-muted-foreground h-4 w-4 shrink-0" />
                <span className="min-w-0 flex-1 font-medium">{tool.name}</span>
                <Badge variant="secondary" className="shrink-0 text-xs">
                  {IMPL_KINDS.find((k) => k.value === tool.implementationKind)
                    ?.label ?? tool.implementationKind}
                </Badge>
                {tool.handlerRef && (
                  <span className="text-muted-foreground max-w-[200px] truncate font-mono text-xs">
                    {tool.handlerRef}
                  </span>
                )}
              </div>
            ))}
          </div>
        )}
      </section>
    </PageShell>
  );
}
