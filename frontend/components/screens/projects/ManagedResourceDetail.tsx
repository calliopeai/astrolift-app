"use client";

import { BoxIcon, RefreshCwIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";

import { type Column, type CursorTableController } from "@/components/data-table";
import { Button } from "@/components/ui/button";
import { ListPage } from "@/components/list/ListPage";
import { useCursorTableList } from "@/components/list/use-cursor-table-list";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { useFormatters } from "@/lib/i18n/formatters";
import type { ListProjectResourceAttachmentsPageQuery } from "@/graphql/__generated__/operations";
import type { ManagedServiceCostPreview } from "./use-project-resources";
import type { ProjectManagedResource } from "./ProjectManagedResourceList";
import { ManagedResourceCost } from "./ManagedResourceCost";

export type ManagedResourceAttachment = NonNullable<
  ListProjectResourceAttachmentsPageQuery["astroliftProjectManagedServiceAttachmentsPage"]
>["items"][number];

export interface ManagedResourceDetailProps {
  target: ProjectManagedResource | null;
  current: ProjectManagedResource | null;
  loading: boolean;
  refused: boolean;
  onClose: () => void;
  onRefresh: () => void;
  attachments: CursorTableController<ManagedResourceAttachment>;
  canUpdate: boolean;
  canPreview: boolean;
  cost?: ManagedServiceCostPreview;
  costLoading: boolean;
  costError: boolean;
  onCost: () => void;
  mutationLoading: boolean;
  onReprovision: () => void;
  onDeprovision: () => void;
  onDetach: (id: string) => void;
  onAttach: (consumer: {
    appEnvironmentId: string | null;
    agentEnvironmentSpecSlug: string | null;
  }) => Promise<void>;
}

/** Exact-GUID metadata and visible consumers; rejected reads never become empty details. */
export function ManagedResourceDetail(props: ManagedResourceDetailProps) {
  const copy = useTranslations("projectResources.reads");
  const operationCopy = useTranslations("projectResources.operation");
  const fmt = useFormatters();
  const attachmentList = useCursorTableList(props.attachments, {
    id: "projects.managed-resource-consumers",
    label: copy("consumers"),
    searchPlaceholder: "",
    searchable: false,
  });
  const [consumer, setConsumer] = React.useState("");
  const [consumerKind, setConsumerKind] = React.useState<"app" | "agent">("app");
  const row = props.current;
  const ready = Boolean(
    row &&
    props.target &&
    row.id === props.target.id &&
    row.contextRevision === props.target.contextRevision &&
    !props.loading &&
    !props.refused
  );
  const busy = props.mutationLoading || props.attachments.isStale;
  const columns: Column<ManagedResourceAttachment>[] = [
    {
      id: "consumer",
      header: copy("consumer"),
      cell: (attachment) => (
        <div className="min-w-0">
          <p className="truncate" title={attachment.consumerSlug}>
            {attachment.consumerSlug}
          </p>
          <p
            className="text-muted-foreground truncate font-mono text-xs"
            title={attachment.consumerId}
          >
            {attachment.consumerId}
          </p>
        </div>
      ),
    },
    {
      id: "environment",
      header: copy("environment"),
      cell: (attachment) =>
        attachment.environmentName ||
        (attachment.consumerKind === "agent_environment_spec" ? copy("agentRecipe") : "—"),
    },
    {
      id: "actions",
      header: copy("actions"),
      cell: (attachment) =>
        props.canUpdate ? (
          <Button
            size="sm"
            variant="ghost"
            disabled={!ready || busy}
            onClick={() => props.onDetach(attachment.id)}
          >
            {copy("detach")}
          </Button>
        ) : null,
    },
  ];
  const identities = row
    ? [
        [copy("organization"), row.organizationId],
        [copy("project"), row.projectId],
        [copy("app"), row.registeredAppId],
        [copy("cluster"), row.clusterId],
        [copy("environment"), row.environmentId || row.environmentName],
      ]
    : [];
  return (
    <Sheet
      open={props.target !== null}
      onOpenChange={(open) => {
        if (!open) props.onClose();
      }}
    >
      <SheetContent className="w-full overflow-y-auto sm:max-w-3xl">
        <SheetHeader>
          <SheetTitle>{props.target?.name || copy("detail")}</SheetTitle>
          <SheetDescription>{copy("detailDescription")}</SheetDescription>
        </SheetHeader>
        <div className="mt-6 space-y-6">
          {props.loading && <Skeleton className="h-32" />}
          {!props.loading && !ready && (
            <div role="alert" className="space-y-2 rounded-md border p-4">
              <p>{copy("refused")}</p>
              <Button variant="outline" onClick={props.onRefresh}>
                {copy("reviewAgain")}
              </Button>
            </div>
          )}
          {ready && row && (
            <>
              <dl className="grid gap-3 sm:grid-cols-2">
                {identities
                  .filter(([, value]) => value)
                  .map(([label, value]) => (
                    <div key={label} className="min-w-0">
                      <dt className="text-muted-foreground text-xs">{label}</dt>
                      <dd className="font-mono text-xs break-all">{value}</dd>
                    </div>
                  ))}
              </dl>
              <p className="text-muted-foreground font-mono text-xs break-all">
                {row.id} · {row.kind}
                {row.variant ? ` · ${row.variant}` : ""}
              </p>
              {row.operationKind && (
                <p className="text-muted-foreground text-xs">
                  {operationCopy("label")} {row.operationKind} ·{" "}
                  {row.operationCompletedAt
                    ? operationCopy("completed", {
                        at: fmt.formatDateTime(row.operationCompletedAt),
                      })
                    : operationCopy("running")}
                </p>
              )}
              {row.operationWorkflowId && (
                <p className="font-mono text-xs break-all">
                  {row.operationWorkflowId}
                  {row.operationRunId ? ` · ${row.operationRunId}` : ""}
                </p>
              )}
              <div className="flex flex-wrap gap-2">
                <Button variant="outline" onClick={props.onRefresh}>
                  <RefreshCwIcon className="size-4" />
                  {copy("refresh")}
                </Button>
                {props.canPreview && (
                  <Button variant="outline" disabled={props.costLoading} onClick={props.onCost}>
                    {copy("costPreview")}
                  </Button>
                )}
                {props.canUpdate && (
                  <>
                    <Button variant="outline" disabled={busy} onClick={props.onReprovision}>
                      {copy("reprovision")}
                    </Button>
                    <Button variant="destructive" disabled={busy} onClick={props.onDeprovision}>
                      {copy("deprovision")}
                    </Button>
                  </>
                )}
              </div>
              {props.costError && (
                <p role="alert" className="text-muted-foreground text-sm">
                  {copy("costUnavailable")}
                </p>
              )}
              {props.cost && <ManagedResourceCost preview={props.cost} />}
              <ListPage
                embedded
                list={attachmentList}
                label={copy("consumers")}
                columns={columns}
                rows={props.attachments.rows}
                getRowId={(attachment) => attachment.id}
                totalCount={props.attachments.totalCount}
                nextCursor={props.attachments.nextCursor ?? null}
                loading={props.attachments.state === "loading"}
                stale={props.attachments.isStale}
                error={props.attachments.state === "error" ? { message: copy("refused") } : null}
                onRetry={props.attachments.retry}
                empty={{
                  icon: <BoxIcon className="size-5" />,
                  title: copy("noConsumers"),
                  description: copy("consumerVisibility"),
                }}
              />
              {props.canUpdate && (
                <form
                  className="space-y-3 rounded-md border p-4"
                  onSubmit={(event) => {
                    event.preventDefault();
                    if (!ready || busy || !consumer.trim()) return;
                    void props.onAttach({
                      appEnvironmentId: consumerKind === "app" ? consumer.trim() : null,
                      agentEnvironmentSpecSlug: consumerKind === "agent" ? consumer.trim() : null,
                    });
                  }}
                >
                  <div className="flex gap-2">
                    <Button
                      type="button"
                      variant={consumerKind === "app" ? "secondary" : "outline"}
                      onClick={() => {
                        setConsumerKind("app");
                        setConsumer("");
                      }}
                    >
                      {copy("appEnvironment")}
                    </Button>
                    <Button
                      type="button"
                      variant={consumerKind === "agent" ? "secondary" : "outline"}
                      onClick={() => {
                        setConsumerKind("agent");
                        setConsumer("");
                      }}
                    >
                      {copy("agentRecipe")}
                    </Button>
                  </div>
                  <Label htmlFor="managed-resource-consumer">
                    {copy(consumerKind === "app" ? "environmentGuid" : "recipeSlug")}
                  </Label>
                  <Input
                    id="managed-resource-consumer"
                    value={consumer}
                    onChange={(event) => setConsumer(event.target.value)}
                    required
                    autoComplete="off"
                  />
                  <p className="text-muted-foreground text-xs">{copy("consumerContext")}</p>
                  <Button type="submit" disabled={busy || !consumer.trim()}>
                    {copy("attach")}
                  </Button>
                </form>
              )}
            </>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}
