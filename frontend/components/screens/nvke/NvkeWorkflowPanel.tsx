"use client";
import { useState } from "react";
import { useForm, useWatch } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { useTranslations } from "next-intl";
import { ActivityIcon } from "lucide-react";
import type { z } from "zod";
import { ListPage } from "@/components/list/ListPage";
import { useCursorTableList } from "@/components/list/use-cursor-table-list";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { type CursorTableController, type Column } from "@/components/data-table";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { DefinitionList } from "@/components/ui/definition-list";
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
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import type { NvkeNativeValues, NvkeNativeWorkflow } from "@/lib/nvke/native-admission";
import {
  exactNativeEventId,
  type NvkeNativeHistoryEvent,
  type NvkeNativeHistoryPage,
} from "@/lib/nvke/native-history";
import { validNvkeGuid } from "@/lib/nvke/recovery";
import { nativeWorkflowFormSchema, nativeResetFormSchema } from "./schema";
import type { NvkeSiteView } from "./types";
import type { NvkeTemporalAuthority } from "./NvkeTemporalPanel";

export interface NvkeWorkflowPanelProps {
  scopeKey: string;
  site: Pick<NvkeSiteView, "id" | "name" | "version" | "isActive">;
  authority: NvkeTemporalAuthority | null;
  source: { operationId: string; version: number };
  clusterId: string;
  owners: Partial<Record<"RenewK8sCertsV2" | "RotateEtcdEncryptionKey", string>>;
  workflows: { name: NvkeNativeWorkflow; schemaText: string }[];
  canManage: boolean;
  canInspect: boolean;
  loading: boolean;
  pending: boolean;
  error: string | null;
  recovery: { requestId: string; operationKey: string } | null;
  history: NvkeNativeHistoryPage | null;
  historyTable: CursorTableController<NvkeNativeHistoryEvent>;
  historyError: string | null;
  onHistory: (workflow: "RenewK8sCertsV2" | "RotateEtcdEncryptionKey") => void;
  onRequest: (values: NvkeNativeValues) => Promise<boolean>;
  onRecover: () => Promise<boolean>;
  onRefresh: () => void;
}
const phases = [
  "prepare",
  "add_secondary",
  "promote_primary",
  "rewrite",
  "remove_old_keys",
  "finalize",
];
const resetDefaults = {
  mode: "",
  rationale: "",
  quiescenceEvidence: "",
  acknowledgeBoundaryReviewed: false,
  acknowledgeConflictingWorkStopped: false,
  retirementWorkflowId: "",
  timeoutSeconds: 7200,
};
export function NvkeWorkflowPanel(props: NvkeWorkflowPanelProps) {
  return (
    <WorkflowEditor
      key={JSON.stringify([
        props.scopeKey,
        props.site.id,
        props.site.version,
        props.authority?.id,
        props.authority?.version,
        props.source,
        props.clusterId,
        props.canManage,
        props.canInspect,
        props.loading,
        props.history?.workflow,
        props.history?.runId,
        props.history?.workflowId,
        props.history?.recordedPhase,
        props.history?.status,
        props.history?.retainedMutationLock,
        props.owners,
      ])}
      {...props}
    />
  );
}
function WorkflowEditor(props: NvkeWorkflowPanelProps) {
  const t = useTranslations("nvke.workflow"),
    common = useTranslations("nvke"),
    m = useTranslations("nvke.maintenance");
  const [workflow, setWorkflow] = useState<NvkeNativeWorkflow>(
    props.workflows[0]?.name ?? "RenewK8sCertsV2"
  );
  const template = (name: NvkeNativeWorkflow) =>
    JSON.stringify(
      name === "RenewK8sCertsV2"
        ? { clusterID: props.clusterId, thresholdDays: 120, force: false }
        : name === "RetireLostEtcdRotationNode"
          ? {
              clusterID: props.clusterId,
              nodeID: "",
              rotationWorkflowID: props.owners.RotateEtcdEncryptionKey ?? "",
            }
          : { clusterID: props.clusterId },
      null,
      2
    );
  const [editing, setEditing] = useState<"workflow" | "reset" | null>(null),
    [selected, setSelected] = useState<NvkeNativeHistoryEvent | null>(null);
  const [review, setReview] = useState<{
    values: NvkeNativeValues;
    nodeId: string | null;
    renewal?: { forced: boolean; threshold: string | null; scheduled: string | null };
  } | null>(null);
  const [invalid, setInvalid] = useState(false);
  const form = useForm<z.infer<typeof nativeWorkflowFormSchema>>({
    resolver: zodResolver(nativeWorkflowFormSchema),
    defaultValues: {
      parametersText: template(workflow),
      timeoutSeconds: 7200,
      acknowledgeLostNodeTeardown: false,
    },
  });
  const reset = useForm<z.infer<typeof nativeResetFormSchema>>({
    resolver: zodResolver(nativeResetFormSchema),
    defaultValues: resetDefaults,
  });
  const mode = useWatch({ control: reset.control, name: "mode" });
  const retireAck = useWatch({ control: form.control, name: "acknowledgeLostNodeTeardown" });
  const boundaryAck = useWatch({ control: reset.control, name: "acknowledgeBoundaryReviewed" });
  const nodeAck = useWatch({ control: reset.control, name: "acknowledgeConflictingWorkStopped" });
  const allowed =
    props.canManage &&
    !props.loading &&
    !props.pending &&
    !props.error &&
    props.site.isActive &&
    !!props.authority?.isEnabled;
  const history = props.history;
  const list = useCursorTableList(props.historyTable, {
    id: "nvke.native-history",
    label: t("history"),
    searchPlaceholder: t("history"),
    searchable: false,
  });
  const bound =
    !!history &&
    history.siteId === props.site.id &&
    history.siteVersion === props.site.version &&
    history.authorityVersion === props.authority?.version &&
    history.sourceOperationId === props.source.operationId &&
    history.sourceVersion === props.source.version &&
    history.clusterId === props.clusterId &&
    history.workflowId === props.owners[history.workflow as keyof typeof props.owners];
  const resetAllowed =
    allowed &&
    bound &&
    history?.workflow === "RotateEtcdEncryptionKey" &&
    history.retainedMutationLock === true &&
    phases.includes(history.recordedPhase ?? "") &&
    ["FAILED", "CANCELED", "TERMINATED", "TIMED_OUT"].includes(history.status) &&
    !props.historyError &&
    !props.historyTable.isStale &&
    props.historyTable.state === "ready";
  const selectable = (event: NvkeNativeHistoryEvent) =>
    resetAllowed &&
    event.eventType === "ACTIVITY_TASK_SCHEDULED" &&
    !!event.activityType &&
    event.activityType !== "BeginEtcdEncryptionKeyRotation" &&
    exactNativeEventId(event.eventId) &&
    exactNativeEventId(event.workflowTaskCompletedEventId);
  const clear = () => {
    setReview(null);
    setEditing(null);
    setSelected(null);
    setInvalid(false);
    form.reset({
      parametersText: template(workflow),
      timeoutSeconds: 7200,
      acknowledgeLostNodeTeardown: false,
    });
    reset.reset(resetDefaults);
  };
  const text = (value: string | null | undefined) => (
    <code className="text-xs [overflow-wrap:anywhere]">{value ?? "—"}</code>
  );
  const columns: Column<NvkeNativeHistoryEvent>[] = [
    { id: "event", header: t("event"), cell: (row) => text(row.eventId) },
    {
      id: "kind",
      header: t("eventType"),
      cellClassName: "max-w-64",
      cell: (row) => (
        <span className="block [overflow-wrap:anywhere]">
          {row.eventType}
          {row.activityType && <code className="block text-xs">{row.activityType}</code>}
        </span>
      ),
    },
    {
      id: "time",
      header: common("observedAt"),
      cell: (row) =>
        row.occurredAt ? <time dateTime={row.occurredAt}>{row.occurredAt}</time> : "—",
    },
    { id: "scheduled", header: t("scheduledEvent"), cell: (row) => text(row.scheduledEventId) },
    { id: "task", header: t("taskEvent"), cell: (row) => text(row.workflowTaskCompletedEventId) },
    {
      id: "recovery",
      header: t("boundary"),
      cell: (row) => (
        <Button
          size="sm"
          variant="outline"
          disabled={!selectable(row)}
          onClick={() => {
            if (!selectable(row)) return;
            setSelected(row);
            reset.reset(resetDefaults);
            setInvalid(false);
            setEditing("reset");
          }}
        >
          {t("selectBoundary")}
        </Button>
      ),
    },
  ];
  const reviewNative = form.handleSubmit((values) => {
    let nodeId: string | null = null;
    try {
      const input: unknown = JSON.parse(values.parametersText);
      if (
        !input ||
        typeof input !== "object" ||
        Array.isArray(input) ||
        (input as Record<string, unknown>).clusterID !== props.clusterId
      )
        throw Error("INVALID");
      if (workflow === "RetireLostEtcdRotationNode") {
        const fields = input as Record<string, unknown>;
        if (
          !validNvkeGuid(fields.nodeID) ||
          !props.owners.RotateEtcdEncryptionKey ||
          fields.rotationWorkflowID !== props.owners.RotateEtcdEncryptionKey ||
          !values.acknowledgeLostNodeTeardown
        )
          throw Error("INVALID");
        nodeId = fields.nodeID;
      }
      setInvalid(false);
      const fields = input as Record<string, unknown>;
      if (
        workflow === "RenewK8sCertsV2" &&
        fields.force !== undefined &&
        typeof fields.force !== "boolean"
      )
        throw Error("INVALID");
      const renewal =
        workflow === "RenewK8sCertsV2"
          ? {
              forced: fields.force === true,
              threshold:
                typeof fields.thresholdDays === "number" &&
                Number.isSafeInteger(fields.thresholdDays)
                  ? String(fields.thresholdDays)
                  : null,
              scheduled:
                typeof fields.scheduledFor === "string" &&
                /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})$/.test(
                  fields.scheduledFor
                ) &&
                Number.isFinite(Date.parse(fields.scheduledFor))
                  ? fields.scheduledFor
                  : null,
            }
          : undefined;
      setReview({ values: { mode: "workflow", workflow, ...values }, nodeId, renewal });
    } catch {
      setInvalid(true);
    }
  });
  const reviewReset = reset.handleSubmit((values) => {
    if (!selected || !selectable(selected) || !history) return;
    const { timeoutSeconds, ...fields } = values;
    setReview({
      nodeId: null,
      values: {
        mode: "reset",
        timeoutSeconds,
        review: {
          ...fields,
          workflowId: history.workflowId,
          baseRunId: history.runId,
          workflowTaskEventId: selected.workflowTaskCompletedEventId!,
          activityEventId: selected.eventId,
          activityType: selected.activityType!,
          phase: history.recordedPhase!,
        },
      },
    });
  });
  const target = review?.values;
  return (
    <Section title={t("title")} description={t("help")}>
      <div className="flex flex-wrap gap-2">
        <Button variant="outline" disabled={props.pending} onClick={props.onRefresh}>
          {common("refresh")}
        </Button>
        {props.canManage && (
          <Button
            disabled={!allowed || !props.workflows.length}
            onClick={() => {
              setInvalid(false);
              setEditing("workflow");
            }}
          >
            {t("submit")}
          </Button>
        )}
        {(["RenewK8sCertsV2", "RotateEtcdEncryptionKey"] as const).map((name) => (
          <Button
            key={name}
            variant="outline"
            disabled={
              !props.canInspect ||
              props.loading ||
              props.pending ||
              !props.authority?.isEnabled ||
              !props.owners[name]
            }
            onClick={() => props.onHistory(name)}
          >
            {t("inspect", { workflow: name })}
          </Button>
        ))}
        {props.recovery && (
          <Button
            variant="outline"
            disabled={!props.canManage || props.pending}
            onClick={props.onRecover}
          >
            {t("recover")}
          </Button>
        )}
      </div>
      {props.error && (
        <p role="alert" className="text-danger-fg text-sm">
          {props.error}
        </p>
      )}
      {props.recovery && (
        <p role="note" className="text-warning-fg text-sm">
          {t("recovery", { id: props.recovery.requestId })} {text(props.recovery.operationKey)}
        </p>
      )}
      {props.loading && <Skeleton className="h-24" />}
      {!props.canManage && <p className="text-muted-foreground text-sm">{t("readOnly")}</p>}
      <DefinitionList
        items={[
          { term: m("clusterId"), description: text(props.clusterId) },
          { term: common("operationId"), description: text(props.source.operationId) },
          { term: common("version"), description: props.source.version },
        ]}
      />
      <Section title={t("history")} description={t("historyHelp")} headingLevel="h3">
        {props.historyError && (
          <p role="alert" className="text-danger-fg text-sm">
            {props.historyError}
          </p>
        )}
        {bound && history ? (
          <>
            <DefinitionList
              items={[
                { term: m("workflow"), description: text(history.workflowId) },
                { term: t("run"), description: text(history.runId) },
                { term: t("nativeStatus"), description: text(history.status) },
                { term: t("recordedState"), description: text(history.recordedState) },
                { term: m("phase"), description: text(history.recordedPhase) },
                { term: common("observedAt"), description: text(history.observedAt) },
                { term: t("sourceTime"), description: text(history.sourceObservedAt) },
              ]}
            />
            <ListPage
              embedded
              list={list}
              label={t("history")}
              rows={props.historyTable.rows}
              loading={props.historyTable.state === "loading"}
              stale={props.historyTable.isStale}
              error={
                props.historyTable.state === "error" ? { message: common("readFailed") } : null
              }
              onRetry={props.historyTable.retry}
              nextCursor={props.historyTable.nextCursor}
              totalCount={props.historyTable.totalCount}
              columns={columns}
              getRowId={(row) => row.eventId}
              empty={{
                icon: <ActivityIcon />,
                title: t("noHistory"),
                description: t("historyHelp"),
              }}
            />
          </>
        ) : (
          <p className="text-muted-foreground text-sm">{t("noHistory")}</p>
        )}
      </Section>
      <Sheet
        open={editing !== null}
        onOpenChange={(open) => {
          if (!open) clear();
        }}
      >
        <SheetContent className="overflow-y-auto sm:max-w-2xl">
          <SheetHeader>
            <SheetTitle>{t(editing === "reset" ? "reset" : "submit")}</SheetTitle>
            <SheetDescription>
              {t(editing === "reset" ? "resetHelp" : "parametersHelp")}
            </SheetDescription>
          </SheetHeader>
          {editing === "workflow" ? (
            <form className="space-y-4 px-4 pb-4" onSubmit={reviewNative}>
              <Label htmlFor="nvke-workflow-choice">{t("chooseWorkflow")}</Label>
              <Select
                value={workflow}
                onValueChange={(value) => {
                  const next = props.workflows.find((item) => item.name === value);
                  if (!next) return;
                  setWorkflow(next.name);
                  setReview(null);
                  setInvalid(false);
                  form.reset({
                    parametersText: template(next.name),
                    timeoutSeconds: 7200,
                    acknowledgeLostNodeTeardown: false,
                  });
                }}
              >
                <SelectTrigger id="nvke-workflow-choice">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {props.workflows.map((item) => (
                    <SelectItem key={item.name} value={item.name}>
                      {item.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Label htmlFor="nvke-workflow-json">{common("parameters")}</Label>
              <Textarea
                id="nvke-workflow-json"
                rows={12}
                spellCheck={false}
                autoComplete="off"
                {...form.register("parametersText")}
              />
              <details>
                <summary>{common("schema")}</summary>
                <pre className="max-h-64 overflow-auto text-xs">
                  {props.workflows.find((item) => item.name === workflow)?.schemaText}
                </pre>
              </details>
              {workflow === "RetireLostEtcdRotationNode" && (
                <Label>
                  <Checkbox
                    checked={retireAck}
                    onCheckedChange={(value) =>
                      form.setValue("acknowledgeLostNodeTeardown", value === true)
                    }
                  />
                  {t("retireAck")}
                </Label>
              )}
              <Label htmlFor="nvke-workflow-timeout">{common("timeout")}</Label>
              <Input
                id="nvke-workflow-timeout"
                type="number"
                min={30}
                max={86400}
                {...form.register("timeoutSeconds", { valueAsNumber: true })}
              />
              {(invalid || Object.keys(form.formState.errors).length > 0) && (
                <p role="alert">{common("invalidForm")}</p>
              )}
              <SheetFooter>
                <Button type="submit" disabled={!allowed}>
                  {common("reviewRequest")}
                </Button>
              </SheetFooter>
            </form>
          ) : editing === "reset" && selected && history ? (
            <form className="space-y-4 px-4 pb-4" onSubmit={reviewReset}>
              <DefinitionList
                items={[
                  { term: m("workflow"), description: text(history.workflowId) },
                  { term: t("run"), description: text(history.runId) },
                  { term: m("phase"), description: text(history.recordedPhase) },
                  { term: t("event"), description: text(selected.eventId) },
                  {
                    term: t("taskEvent"),
                    description: text(selected.workflowTaskCompletedEventId),
                  },
                  { term: common("action"), description: text(selected.activityType) },
                ]}
              />
              <Label htmlFor="nvke-reset-mode">{t("mode")}</Label>
              <Select
                value={mode}
                onValueChange={(value) => {
                  if (value) {
                    reset.setValue("mode", value);
                    reset.setValue("acknowledgeBoundaryReviewed", false);
                    reset.setValue("acknowledgeConflictingWorkStopped", false);
                    if (value !== "refresh_survivors") reset.setValue("retirementWorkflowId", "");
                  }
                }}
              >
                <SelectTrigger id="nvke-reset-mode">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {["retry_activity", "restart_rewrite", "resume_rewrite", "refresh_survivors"].map(
                    (value) => (
                      <SelectItem key={value} value={value}>
                        {value}
                      </SelectItem>
                    )
                  )}
                </SelectContent>
              </Select>
              {mode === "refresh_survivors" && (
                <>
                  <Label htmlFor="nvke-reset-retirement">{t("retirement")}</Label>
                  <Input
                    id="nvke-reset-retirement"
                    autoComplete="off"
                    {...reset.register("retirementWorkflowId")}
                  />
                </>
              )}
              <Label htmlFor="nvke-reset-rationale">{t("rationale")}</Label>
              <Textarea
                id="nvke-reset-rationale"
                autoComplete="off"
                {...reset.register("rationale")}
              />
              <Label htmlFor="nvke-reset-evidence">{t("evidence")}</Label>
              <Textarea
                id="nvke-reset-evidence"
                autoComplete="off"
                {...reset.register("quiescenceEvidence")}
              />
              <Label>
                <Checkbox
                  checked={boundaryAck}
                  onCheckedChange={(value) =>
                    reset.setValue("acknowledgeBoundaryReviewed", value === true)
                  }
                />
                {t("boundaryAck")}
              </Label>
              <Label>
                <Checkbox
                  checked={nodeAck}
                  onCheckedChange={(value) =>
                    reset.setValue("acknowledgeConflictingWorkStopped", value === true)
                  }
                />
                {t("nodeAck")}
              </Label>
              <Label htmlFor="nvke-reset-timeout">{common("timeout")}</Label>
              <Input
                id="nvke-reset-timeout"
                type="number"
                min={30}
                max={86400}
                {...reset.register("timeoutSeconds", { valueAsNumber: true })}
              />
              {Object.keys(reset.formState.errors).length > 0 && (
                <p role="alert">{common("invalidForm")}</p>
              )}
              <SheetFooter>
                <Button type="submit" disabled={!resetAllowed}>
                  {common("reviewRequest")}
                </Button>
              </SheetFooter>
            </form>
          ) : null}
        </SheetContent>
      </Sheet>
      <ConfirmDialog
        open={review !== null}
        onOpenChange={(open) => {
          if (!open) setReview(null);
        }}
        title={t(target?.mode === "reset" ? "reset" : "submit")}
        destructive
        confirmDisabled={!allowed || (target?.mode === "reset" && !resetAllowed)}
        description={
          <>
            {common("temporalReview", {
              site: props.site.name,
              siteVersion: props.site.version,
              version: props.authority?.version ?? 0,
              address: props.authority?.address ?? "—",
              namespace: props.authority?.namespace ?? "—",
              queue: props.authority?.taskQueue ?? "—",
            })}
            <span className="mt-2 block">{t("reviewHelp")}</span>
            <span
              role="region"
              aria-label={common("reviewRequest")}
              tabIndex={0}
              className="text-foreground mt-2 block max-h-96 overflow-y-auto text-left [overflow-wrap:anywhere]"
            >
              {[
                { term: m("clusterId"), description: text(props.clusterId) },
                { term: common("operationId"), description: text(props.source.operationId) },
                { term: common("version"), description: props.source.version },
                {
                  term: common("action"),
                  description: text(
                    target?.mode === "workflow" ? target.workflow : "RotateEtcdEncryptionKey"
                  ),
                },
                ...(review?.renewal
                  ? [
                      { term: m("forced"), description: m(review.renewal.forced ? "yes" : "no") },
                      {
                        term: "thresholdDays",
                        description: text(
                          review.renewal.threshold ?? common("targetFromParameters")
                        ),
                      },
                      { term: m("scheduled"), description: text(review.renewal.scheduled) },
                    ]
                  : []),
                ...(target?.mode === "workflow" && target.workflow === "RetireLostEtcdRotationNode"
                  ? [
                      {
                        term: m("workflow"),
                        description: text(props.owners.RotateEtcdEncryptionKey),
                      },
                    ]
                  : []),
                ...(review?.nodeId
                  ? [{ term: t("targetNode"), description: text(review.nodeId) }]
                  : []),
                ...(target?.mode === "reset"
                  ? [
                      { term: m("workflow"), description: text(target.review.workflowId) },
                      { term: t("run"), description: text(target.review.baseRunId) },
                      { term: t("event"), description: text(target.review.activityEventId) },
                      {
                        term: t("taskEvent"),
                        description: text(target.review.workflowTaskEventId),
                      },
                      { term: m("phase"), description: text(target.review.phase) },
                      { term: t("mode"), description: text(target.review.mode) },
                      ...(target.review.mode === "refresh_survivors"
                        ? [
                            {
                              term: t("retirement"),
                              description: text(target.review.retirementWorkflowId),
                            },
                          ]
                        : []),
                    ]
                  : []),
              ].map((item, index) => (
                <span className="mt-1 block" key={index}>
                  <strong>{item.term}: </strong>
                  {item.description}
                </span>
              ))}
            </span>
          </>
        }
        onConfirm={async () => {
          if (!allowed || !review || (review.values.mode === "reset" && !resetAllowed))
            return false;
          const values = structuredClone(review.values);
          clear();
          try {
            return await props.onRequest(values);
          } catch {
            return false;
          }
        }}
      />
    </Section>
  );
}
