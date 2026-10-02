"use client";

import { BoxIcon } from "lucide-react";
import { useTranslations } from "next-intl";

import { type Column, type CursorTableController } from "@/components/data-table";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ListPage } from "@/components/list/ListPage";
import { useCursorTableList } from "@/components/list/use-cursor-table-list";
import type { ManagedResourceContextFragment } from "@/graphql/__generated__/operations";

export type ProjectManagedResource = ManagedResourceContextFragment;
const RESOURCE_STATUSES = new Set([
  "pending",
  "provisioning",
  "active",
  "updating",
  "deprovisioning",
  "failed",
]);

export interface ProjectManagedResourceListProps {
  controller: CursorTableController<ProjectManagedResource>;
  onOpen: (resource: ProjectManagedResource) => void;
}

/** Project-owned infrastructure only; app-owned dependencies have their own list. */
export function ProjectManagedResourceList({
  controller,
  onOpen,
}: ProjectManagedResourceListProps) {
  const copy = useTranslations("projectResources.reads");
  const list = useCursorTableList(controller, {
    id: "projects.managed-resources",
    label: copy("title"),
    searchPlaceholder: copy("search"),
  });
  const columns: Column<ProjectManagedResource>[] = [
    {
      id: "name",
      header: copy("name"),
      cell: (row) => (
        <div className="min-w-0">
          <Button
            variant="link"
            className="h-auto max-w-full justify-start p-0"
            disabled={controller.isStale || controller.state === "error"}
            onClick={() => onOpen(row)}
          >
            <span className="truncate" title={row.name}>
              {row.name}
            </span>
          </Button>
          <p className="text-muted-foreground truncate font-mono text-xs" title={row.id}>
            {row.id}
          </p>
        </div>
      ),
    },
    {
      id: "kind",
      header: copy("kind"),
      cell: (row) => (
        <span className="font-mono text-xs">
          {row.kind}
          {row.variant ? ` · ${row.variant}` : ""}
        </span>
      ),
    },
    {
      id: "cluster",
      header: copy("cluster"),
      cell: (row) => (
        <div className="min-w-0">
          <p className="truncate" title={row.clusterSlug}>
            {row.clusterSlug}
          </p>
          <p
            className="text-muted-foreground truncate font-mono text-xs"
            title={row.environmentName}
          >
            {row.environmentName}
          </p>
        </div>
      ),
    },
    {
      id: "status",
      header: copy("status"),
      cell: (row) => (
        <Badge variant={row.status === "failed" ? "destructive" : "secondary"}>
          {RESOURCE_STATUSES.has(row.status) ? copy(`statuses.${row.status}`) : row.status}
        </Badge>
      ),
    },
  ];
  return (
    <ListPage
      embedded
      list={list}
      label={copy("title")}
      columns={columns}
      rows={controller.rows}
      getRowId={(row) => row.id}
      totalCount={controller.totalCount}
      nextCursor={controller.nextCursor ?? null}
      loading={controller.state === "loading"}
      stale={controller.isStale}
      error={controller.state === "error" ? { message: copy("refused") } : null}
      onRetry={controller.retry}
      empty={{
        icon: <BoxIcon className="size-5" />,
        title: copy("empty"),
        description: copy("emptyDescription"),
      }}
    />
  );
}
