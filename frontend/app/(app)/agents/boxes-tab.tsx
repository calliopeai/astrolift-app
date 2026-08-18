"use client";

import { useMutation } from "@apollo/client/react";
import {
  BoxIcon,
  CopyIcon,
  Loader2Icon,
  PlusIcon,
  TerminalIcon,
  Trash2Icon,
} from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { DataTable, useCursorTable, type Column } from "@/components/data-table";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  DESTROY_AGENT_BOX,
  ENSURE_AGENT_BOX,
} from "@/graphql/agents/agents.mutations";
import {
  LIST_AGENT_BOXES,
  LIST_AGENT_ENVIRONMENT_SPECS,
} from "@/graphql/agents/agents.queries";
import type {
  AgentBoxesData,
  AstroliftAgentBox,
  AstroliftAgentEnvironmentSpec,
} from "@/graphql/agents/agents.types";
import type {
  DestroyAgentBoxMutation,
  EnsureAgentBoxMutation,
} from "@/graphql/__generated__/operations";
import { useQuery } from "@apollo/client/react";
import { formatRelativeAge } from "@/lib/format";

// The presets the dialog offers. A box that is never reaped is a node burning
// indefinitely, so "Never" is a labelled choice at the bottom of the list
// rather than a blank field that happens to mean forever.
const IDLE_PRESETS: { value: string; label: string }[] = [
  { value: "900", label: "15 minutes" },
  { value: "3600", label: "1 hour" },
  { value: "14400", label: "4 hours" },
  { value: "86400", label: "1 day" },
  { value: "0", label: "Never — holds the node until destroyed" },
];

const LIVE_STATUSES = new Set(["pending", "provisioning", "running"]);

function statusVariant(
  status: string,
): "default" | "secondary" | "outline" | "destructive" {
  if (status === "running") return "default";
  if (status === "failed") return "destructive";
  if (LIVE_STATUSES.has(status)) return "secondary";
  return "outline";
}

function formatIdle(seconds: number): string {
  if (seconds === 0) return "Never";
  if (seconds % 86400 === 0) return `${seconds / 86400}d`;
  if (seconds % 3600 === 0) return `${seconds / 3600}h`;
  if (seconds % 60 === 0) return `${seconds / 60}m`;
  return `${seconds}s`;
}

/** The command that lands an operator inside the box. */
function attachLine(box: AstroliftAgentBox): string {
  return `astro exec --app ${box.slug} -- ${box.attachCommand.join(" ")}`;
}

function AttachCell({ box }: { box: AstroliftAgentBox }) {
  if (!LIVE_STATUSES.has(box.status)) {
    return <span className="text-muted-foreground text-sm">—</span>;
  }
  const line = attachLine(box);
  return (
    <div className="flex items-center gap-1">
      <code className="bg-muted truncate rounded px-1.5 py-0.5 font-mono text-xs">
        {line}
      </code>
      <Button
        size="icon"
        variant="ghost"
        aria-label={`Copy attach command for ${box.slug}`}
        className="size-6 shrink-0"
        onClick={() => {
          void navigator.clipboard.writeText(line);
          toast.success("Attach command copied");
        }}
      >
        <CopyIcon className="size-3.5" />
      </Button>
    </div>
  );
}

export function BoxesTab({ orgId }: { orgId: string }) {
  const [dialogOpen, setDialogOpen] = React.useState(false);
  const [specSlug, setSpecSlug] = React.useState("");
  const [idle, setIdle] = React.useState("3600");
  const [name, setName] = React.useState("");
  const [includeEnded, setIncludeEnded] = React.useState(false);

  // `agentBoxes` is a capped list rather than a cursor page — an org has a
  // handful of boxes, not a fleet — so the envelope is synthesized here. The
  // items array is Apollo's, so row identity stays stable across renders.
  const extract = React.useCallback((d: unknown) => {
    const rows = (d as AgentBoxesData | undefined)?.agentBoxes;
    return rows ? { items: rows, nextCursor: null, totalCount: rows.length } : null;
  }, []);

  const controller = useCursorTable<AstroliftAgentBox, { orgId: string }>({
    query: LIST_AGENT_BOXES,
    variables: { orgId, includeEnded } as Partial<{ orgId: string }>,
    extract,
    // A box takes a moment to go provisioning -> running, so the button press
    // visibly resolves instead of leaving the operator to reload.
    pollInterval: 5000,
    skip: !orgId,
  });

  const { data: specData } = useQuery<{
    agentEnvironmentSpecs: AstroliftAgentEnvironmentSpec[];
  }>(LIST_AGENT_ENVIRONMENT_SPECS, { variables: { orgId }, skip: !orgId });
  const specs = specData?.agentEnvironmentSpecs ?? [];

  const [ensureBox, { loading: starting }] =
    useMutation<EnsureAgentBoxMutation>(ENSURE_AGENT_BOX);
  const [destroyBox] = useMutation<DestroyAgentBoxMutation>(DESTROY_AGENT_BOX);

  async function handleStart() {
    if (!specSlug) return;
    try {
      const { data: resp } = await ensureBox({
        variables: {
          orgId,
          input: {
            environmentSpecSlug: specSlug,
            name: name.trim(),
            idleTimeoutSeconds: Number(idle),
          },
        },
      });
      const result = resp?.ensureAgentBox;
      if (!result?.ok) {
        throw new Error(result?.errors?.[0]?.message ?? "Could not start the box");
      }
      toast.success(`Box ${result.data?.slug ?? ""} is starting`);
      setDialogOpen(false);
      setName("");
      controller.refetch();
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error("Couldn't start the box", { description: message });
    }
  }

  async function handleDestroy(box: AstroliftAgentBox) {
    try {
      const { data: resp } = await destroyBox({ variables: { slug: box.slug } });
      const result = resp?.destroyAgentBox;
      if (!result?.ok) {
        throw new Error(result?.errors?.[0]?.message ?? "Could not destroy the box");
      }
      toast.success(`Destroyed ${box.slug}`);
      controller.refetch();
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error("Couldn't destroy the box", { description: message });
    }
  }

  const columns: Column<AstroliftAgentBox>[] = [
    {
      id: "box",
      header: "Box",
      cell: (box) => (
        <div>
          <div className="font-medium">{box.name}</div>
          <div className="text-muted-foreground font-mono text-xs">{box.slug}</div>
        </div>
      ),
    },
    {
      id: "status",
      header: "Status",
      width: "w-40",
      cell: (box) => (
        <div>
          <Badge variant={statusVariant(box.status)}>{box.status}</Badge>
          {box.lastError && (
            <div className="text-muted-foreground mt-1 line-clamp-2 text-xs">
              {box.lastError}
            </div>
          )}
        </div>
      ),
    },
    {
      id: "agent",
      header: "Agent",
      cell: (box) => (
        <span className="text-sm">
          {box.agentSlug || box.environmentSpecSlug || "—"}
        </span>
      ),
    },
    {
      id: "idle",
      header: "Idle timeout",
      width: "w-28",
      cell: (box) => <span className="text-sm">{formatIdle(box.idleTimeoutSeconds)}</span>,
    },
    {
      id: "attach",
      header: "Attach",
      cell: (box) => <AttachCell box={box} />,
    },
    {
      id: "started",
      header: "Started",
      width: "w-28",
      cell: (box) => (
        <span className="text-muted-foreground text-sm">
          {box.startedAt ? formatRelativeAge(box.startedAt) : "—"}
        </span>
      ),
    },
    {
      id: "actions",
      header: "",
      align: "right",
      width: "w-32",
      cell: (box) =>
        LIVE_STATUSES.has(box.status) ? (
          <Button size="sm" variant="outline" onClick={() => void handleDestroy(box)}>
            <Trash2Icon className="size-4" />
            Destroy
          </Button>
        ) : null,
    },
  ];

  return (
    <div className="space-y-4">
      <DataTable<AstroliftAgentBox>
        label="Agent boxes"
        controller={controller}
        columns={columns}
        getRowId={(box) => box.id}
        empty={{
          icon: <BoxIcon className="size-5" />,
          title: "No agent boxes",
          description:
            "Start one to get a container running Claude Code, Codex, or the Calliope CLI that you can attach a terminal to. tmux holds the session open, so a dropped connection does not kill the agent.",
        }}
        toolbar={
          <div className="flex items-center gap-2">
            <Button size="sm" variant="ghost" onClick={() => setIncludeEnded((v) => !v)}>
              {includeEnded ? "Live only" : "Show ended"}
            </Button>
            <Button size="sm" onClick={() => setDialogOpen(true)}>
              <PlusIcon className="size-4" />
              New agent box
            </Button>
          </div>
        }
      />

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>New agent box</DialogTitle>
            <DialogDescription>
              Starts a container from an environment spec — its image and its secret packet
              (ANTHROPIC_API_KEY and friends). Pressing this again for the same spec attaches to
              the box you already have rather than starting a second one.
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="box-spec">Environment spec</Label>
              <Select value={specSlug} onValueChange={setSpecSlug}>
                <SelectTrigger id="box-spec">
                  <SelectValue placeholder="Pick an agent environment" />
                </SelectTrigger>
                <SelectContent>
                  {specs.map((spec) => (
                    <SelectItem key={spec.id} value={spec.slug}>
                      {spec.name} ({spec.runtime || spec.agentType})
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {specs.length === 0 && (
                <p className="text-muted-foreground text-xs">
                  No environment specs yet — create one first so the box knows which image to run
                  and which secrets to carry.
                </p>
              )}
            </div>

            <div className="space-y-2">
              <Label htmlFor="box-idle">Idle timeout</Label>
              <Select value={idle} onValueChange={setIdle}>
                <SelectTrigger id="box-idle">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {IDLE_PRESETS.map((preset) => (
                    <SelectItem key={preset.value} value={preset.value}>
                      {preset.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-muted-foreground text-xs">
                Measured from the last pane activity, not the last attach — an agent working while
                you are away keeps its box.
              </p>
            </div>

            <div className="space-y-2">
              <Label htmlFor="box-name">Name (optional)</Label>
              <Input
                id="box-name"
                value={name}
                placeholder="Leave blank to name it after the agent"
                onChange={(e) => setName(e.target.value)}
              />
            </div>
          </div>

          <div className="flex justify-end gap-2 pt-2">
            <Button variant="outline" onClick={() => setDialogOpen(false)}>
              Cancel
            </Button>
            <Button disabled={!specSlug || starting} onClick={() => void handleStart()}>
              {starting ? (
                <Loader2Icon className="size-4 animate-spin" />
              ) : (
                <TerminalIcon className="size-4" />
              )}
              Start box
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
