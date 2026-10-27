"use client";

import * as React from "react";
import Link from "next/link";
import { ArrowLeftIcon, ArrowUpRightIcon, FocusIcon, ExpandIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import { PageShell } from "@/components/PageShell";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { FlowGraph } from "@/components/viz/flow-graph";
import { ProjectTopologyNode } from "./ProjectTopologyNode";
import { buildProjectTopology, type ProjectTopologyInput } from "./project-topology-model";
import type { ProjectDetailProject } from "./use-project-detail";

const nodeTypes = { component: ProjectTopologyNode };
export type ProjectTopologyScreenProps = ProjectTopologyInput & {
  slug: string;
  project: ProjectDetailProject | undefined | null;
  loading: boolean;
  partialError: boolean;
  focus: string;
  selectedId: string;
  onFocus: (id: string) => void;
  onSelect: (id: string) => void;
};

export function ProjectTopologyScreen({
  slug,
  project,
  loading,
  partialError,
  focus,
  selectedId,
  onFocus,
  onSelect,
  ...input
}: ProjectTopologyScreenProps) {
  const t = useTranslations("projectTopology");
  const [expanded, setExpanded] = React.useState(false);
  const [listView, setListView] = React.useState(false);
  const graph = buildProjectTopology(input, focus);
  const selected = graph.nodes.find((node) => node.id === selectedId);
  const connections = selected
    ? graph.edges.filter((edge) => edge.source === selected.id || edge.target === selected.id)
    : [];
  const structureKey = focus + graph.nodes.map((node) => node.id).join("|");
  const body = (
    <>
      <div className="flex flex-wrap items-center gap-3">
        <label className="flex items-center gap-2 text-sm">
          {t("scope")}
          <select
            className="bg-background rounded-md border px-3 py-2"
            value={focus}
            onChange={(event) => onFocus(event.target.value)}
          >
            <option value="">{t("wholeProject")}</option>
            {input.workflows.map((workflow) => (
              <option key={workflow.guid} value={workflow.guid}>
                {workflow.name}
              </option>
            ))}
          </select>
        </label>
        <Button variant="outline" onClick={() => setListView((value) => !value)}>
          {listView ? t("canvas") : t("componentList")}
        </Button>
        {!expanded && (
          <Button variant="outline" onClick={() => setExpanded(true)}>
            <ExpandIcon className="size-4" />
            {t("expand")}
          </Button>
        )}
        <span className="text-muted-foreground text-xs">
          {t("counts", { nodes: graph.nodes.length, edges: graph.edges.length })}
        </span>
      </div>
      {partialError && (
        <p role="status" className="text-warning-fg text-sm">
          {t("partialError")}
        </p>
      )}
      {loading ? (
        <Skeleton className="h-96 w-full" />
      ) : !project ? (
        <p role="status">{t("notFound")}</p>
      ) : graph.nodes.length === 0 ? (
        <p role="status">{t("empty")}</p>
      ) : (
        <div className="grid min-h-0 gap-4 xl:grid-cols-[minmax(0,1fr)_22rem]">
          <div className="min-w-0">
            {listView ? (
              <div
                className="flex max-h-[75dvh] flex-col gap-1 overflow-auto rounded-lg border p-3"
                aria-label={t("componentList")}
              >
                {graph.nodes.map((node) => (
                  <Button
                    key={node.id}
                    variant={node.id === selectedId ? "secondary" : "ghost"}
                    className="h-auto justify-start text-left whitespace-normal"
                    onClick={() => onSelect(node.id)}
                  >
                    {t(`kind.${node.data.kind}`)} · {node.data.label}
                  </Button>
                ))}
              </div>
            ) : (
              <FlowGraph
                key={structureKey}
                nodes={graph.nodes.map((node) => ({ ...node, selected: node.id === selectedId }))}
                edges={graph.edges.map((edge) => ({
                  ...edge,
                  label: t(`edge.${edge.label}`),
                  labelStyle: { fill: "var(--muted-foreground)", fontSize: 11 },
                  labelBgStyle: { fill: "var(--card)" },
                }))}
                nodeTypes={nodeTypes}
                height="75dvh"
                minZoom={0.2}
                maxZoom={2}
                fitViewOptions={{ padding: 0.2, maxZoom: 1 }}
                onNodeClick={onSelect}
                ariaLabel={t("canvas")}
              />
            )}
          </div>
          <aside
            className="bg-card max-h-[75dvh] overflow-auto rounded-lg border p-5"
            aria-label={t("inspector")}
            aria-live="polite"
          >
            {selected ? (
              <>
                <p className="text-muted-foreground text-xs uppercase">
                  {t(`kind.${selected.data.kind}`)}
                </p>
                <h2 className="mt-2 text-xl font-semibold break-words">{selected.data.label}</h2>
                <p className="text-muted-foreground mt-2 text-sm">{selected.data.description}</p>
                <p className="my-3 text-sm">
                  {t("status")}: {selected.data.status}
                </p>
                {selected.data.href && (
                  <Button asChild className="mb-3 w-full">
                    <Link href={selected.data.href}>
                      {t("seeMore")}
                      <ArrowUpRightIcon className="size-4" />
                    </Link>
                  </Button>
                )}
                {selected.data.kind === "workflow" && selected.data.workflowId && !focus && (
                  <Button
                    className="mb-4 w-full"
                    variant="outline"
                    onClick={() => onFocus(selected.data.workflowId!)}
                  >
                    <FocusIcon className="size-4" />
                    {t("exploreWorkflow")}
                  </Button>
                )}
                <dl className="space-y-3 border-t py-4 text-sm">
                  {selected.data.fields.map((field) => (
                    <div key={field.label}>
                      <dt className="text-muted-foreground text-xs">{t(`field.${field.label}`)}</dt>
                      <dd className="mt-1 break-words">
                        {field.href ? (
                          <Link className="text-primary underline" href={field.href}>
                            {field.value}
                          </Link>
                        ) : (
                          field.value
                        )}
                      </dd>
                    </div>
                  ))}
                </dl>
                <h3 className="text-sm font-semibold">{t("connections")}</h3>
                <div className="mt-2 flex flex-col gap-1">
                  {connections.map((edge) => {
                    const other = graph.nodes.find(
                      (node) =>
                        node.id === (edge.source === selected.id ? edge.target : edge.source)
                    );
                    return (
                      other && (
                        <Button
                          key={edge.id}
                          variant="ghost"
                          className="h-auto justify-start text-left text-xs whitespace-normal"
                          onClick={() => onSelect(other.id)}
                        >
                          {t(`edge.${edge.label}`)} · {other.data.label}
                        </Button>
                      )
                    );
                  })}
                </div>
              </>
            ) : (
              <>
                <h2 className="font-semibold">{t("selectTitle")}</h2>
                <p className="text-muted-foreground mt-2 text-sm">{t("selectDescription")}</p>
              </>
            )}
            <p className="text-muted-foreground mt-6 border-t pt-4 text-xs">{t("evidenceNote")}</p>
          </aside>
        </div>
      )}
    </>
  );
  return (
    <PageShell
      title={t("title", { project: project?.name ?? slug })}
      description={t("description")}
      actions={
        <Button asChild variant="outline">
          <Link href={`/projects/${encodeURIComponent(slug)}`}>
            <ArrowLeftIcon className="size-4" />
            {t("back")}
          </Link>
        </Button>
      }
    >
      {body}
      <Dialog open={expanded} onOpenChange={setExpanded}>
        <DialogContent className="flex h-[calc(100dvh-2rem)] w-[calc(100vw-2rem)] max-w-none flex-col overflow-auto p-5 sm:max-w-none">
          <DialogTitle>{t("title", { project: project?.name ?? slug })}</DialogTitle>
          <DialogDescription>{t("description")}</DialogDescription>
          {expanded && body}
        </DialogContent>
      </Dialog>
    </PageShell>
  );
}
