"use client";

import { BoxIcon, CopyIcon, Loader2Icon, PlusIcon, TerminalIcon, Trash2Icon } from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";

import type { Column } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import { agentsCrumbs } from "@/components/screens/agents/skills/catalog";
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
import type { AstroliftAgentBox } from "@/graphql/agents/agents.types";
import { useFormatters } from "@/lib/i18n/formatters";

import type { useAgentBoxes } from "./use-agent-boxes";

export type BoxesTabViewProps = ReturnType<typeof useAgentBoxes>;

// The presets the dialog offers. A box that is never reaped is a node burning
// indefinitely, so "Never" is a labelled choice at the bottom of the list
// rather than a blank field that happens to mean forever.
const IDLE_PRESETS: { value: string; labelKey: "m15" | "h1" | "h4" | "d1" | "never" }[] = [
  { value: "900", labelKey: "m15" },
  { value: "3600", labelKey: "h1" },
  { value: "14400", labelKey: "h4" },
  { value: "86400", labelKey: "d1" },
  { value: "0", labelKey: "never" },
];

const LIVE_STATUSES = new Set(["pending", "provisioning", "running"]);

function statusVariant(status: string): "default" | "secondary" | "outline" | "destructive" {
  if (status === "running") return "default";
  if (status === "failed") return "destructive";
  if (LIVE_STATUSES.has(status)) return "secondary";
  return "outline";
}

function formatIdle(seconds: number, t: ReturnType<typeof useTranslations<"agentBoxes">>): string {
  if (seconds === 0) return t("idle.never");
  if (seconds % 86400 === 0) return t("idle.days", { n: seconds / 86400 });
  if (seconds % 3600 === 0) return t("idle.hours", { n: seconds / 3600 });
  if (seconds % 60 === 0) return t("idle.minutes", { n: seconds / 60 });
  return t("idle.seconds", { n: seconds });
}

/** The command that lands an operator inside the box. */
function attachLine(box: AstroliftAgentBox): string {
  return `astro exec --app ${box.slug} -- ${box.attachCommand.join(" ")}`;
}

function AttachCell({ box, onCopy }: { box: AstroliftAgentBox; onCopy: (line: string) => void }) {
  const t = useTranslations("agentBoxes");
  if (!LIVE_STATUSES.has(box.status)) {
    return <span className="text-muted-foreground text-sm">—</span>;
  }
  const line = attachLine(box);
  return (
    <div className="flex items-center gap-1">
      <code className="bg-muted truncate rounded px-1.5 py-0.5 font-mono text-xs">{line}</code>
      <Button
        size="icon"
        variant="ghost"
        aria-label={t("copyAttachFor", { slug: box.slug })}
        className="size-6 shrink-0"
        onClick={() => onCopy(line)}
      >
        <CopyIcon className="size-3.5" />
      </Button>
    </div>
  );
}

/**
 * Agent boxes: long-lived attachable agent containers on the shared list
 * (views All · Mine, a status filter, numbered pages over the org's boxes;
 * see agent-boxes-list.ts), and the new-box dialog. Pure view; the data half
 * is useAgentBoxes.
 */
export function BoxesTabView({
  list,
  rows,
  totalCount,
  loading,
  stale,
  error,
  onRetry,
  specs,
  starting,
  includeEnded,
  toggleIncludeEnded,
  startBox,
  destroyBox,
  copyAttach,
}: BoxesTabViewProps) {
  const t = useTranslations("agentBoxes");
  const fmt = useFormatters();
  const [dialogOpen, setDialogOpen] = React.useState(false);
  const [specSlug, setSpecSlug] = React.useState("");
  const [idle, setIdle] = React.useState("3600");
  const [name, setName] = React.useState("");

  async function handleStart() {
    if (await startBox({ specSlug, name, idle })) {
      setDialogOpen(false);
      setName("");
    }
  }

  const columns: Column<AstroliftAgentBox>[] = [
    {
      id: "box",
      header: t("columns.box"),
      sortKey: "name",
      cell: (box) => (
        <div>
          <div className="font-medium">{box.name}</div>
          <div className="text-muted-foreground font-mono text-xs">{box.slug}</div>
        </div>
      ),
    },
    {
      id: "status",
      header: t("columns.status"),
      sortKey: "status",
      width: "w-40",
      cell: (box) => (
        <div>
          <Badge variant={statusVariant(box.status)}>{box.status}</Badge>
          {box.lastError && (
            <div className="text-muted-foreground mt-1 line-clamp-2 text-xs">{box.lastError}</div>
          )}
        </div>
      ),
    },
    {
      id: "agent",
      header: t("columns.agent"),
      cell: (box) => (
        <span className="text-sm">{box.agentSlug || box.environmentSpecSlug || "—"}</span>
      ),
    },
    {
      id: "idle",
      header: t("columns.idle"),
      sortKey: "idle",
      width: "w-28",
      cell: (box) => <span className="text-sm">{formatIdle(box.idleTimeoutSeconds, t)}</span>,
    },
    {
      id: "attach",
      header: t("columns.attach"),
      cell: (box) => <AttachCell box={box} onCopy={copyAttach} />,
    },
    {
      id: "started",
      header: t("columns.started"),
      sortKey: "started",
      width: "w-28",
      cell: (box) => (
        <span className="text-muted-foreground text-sm">
          {box.startedAt ? fmt.formatRelativeTime(box.startedAt) : "—"}
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
          <Button size="sm" variant="outline" onClick={() => void destroyBox(box)}>
            <Trash2Icon className="size-4" />
            {t("destroy")}
          </Button>
        ) : null,
    },
  ];

  return (
    <>
      <ListPage<AstroliftAgentBox>
        header={{
          crumbs: agentsCrumbs("workloads", { label: t("title") }),
          title: t("title"),
          primaryAction: (
            <Button size="sm" onClick={() => setDialogOpen(true)}>
              <PlusIcon className="size-4" />
              {t("newBox")}
            </Button>
          ),
          menu: (
            <Button size="sm" variant="ghost" onClick={toggleIncludeEnded}>
              {includeEnded ? t("liveOnly") : t("showEnded")}
            </Button>
          ),
        }}
        list={list}
        label={t("title")}
        columns={columns}
        rows={rows}
        getRowId={(box) => box.id}
        loading={loading}
        stale={stale}
        error={error}
        onRetry={onRetry}
        totalCount={totalCount}
        empty={{
          icon: <BoxIcon className="size-5" />,
          title: t("emptyTitle"),
          description: t("emptyDescription"),
        }}
      />

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("newBox")}</DialogTitle>
            <DialogDescription>{t("dialogDescription")}</DialogDescription>
          </DialogHeader>

          <div className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="box-spec">{t("envSpec")}</Label>
              <Select value={specSlug} onValueChange={setSpecSlug}>
                <SelectTrigger id="box-spec">
                  <SelectValue placeholder={t("pickEnv")} />
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
                <p className="text-muted-foreground text-xs">{t("noSpecs")}</p>
              )}
            </div>

            <div className="space-y-2">
              <Label htmlFor="box-idle">{t("idleTimeout")}</Label>
              <Select value={idle} onValueChange={setIdle}>
                <SelectTrigger id="box-idle">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {IDLE_PRESETS.map((preset) => (
                    <SelectItem key={preset.value} value={preset.value}>
                      {t(`presets.${preset.labelKey}`)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-muted-foreground text-xs">{t("idleHelp")}</p>
            </div>

            <div className="space-y-2">
              <Label htmlFor="box-name">{t("nameOptional")}</Label>
              <Input
                id="box-name"
                value={name}
                placeholder={t("namePlaceholder")}
                onChange={(e) => setName(e.target.value)}
              />
            </div>
          </div>

          <div className="flex justify-end gap-2 pt-2">
            <Button variant="outline" onClick={() => setDialogOpen(false)}>
              {t("cancel")}
            </Button>
            <Button disabled={!specSlug || starting} onClick={() => void handleStart()}>
              {starting ? (
                <Loader2Icon className="size-4 animate-spin" />
              ) : (
                <TerminalIcon className="size-4" />
              )}
              {t("start")}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
