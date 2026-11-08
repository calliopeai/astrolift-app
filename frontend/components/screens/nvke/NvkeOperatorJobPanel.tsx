"use client";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { zodResolver } from "@hookform/resolvers/zod";
import { useTranslations } from "next-intl";
import { DownloadIcon, FileLockIcon } from "lucide-react";
import type { Column, CursorTableController } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import { useCursorTableList } from "@/components/list/use-cursor-table-list";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Button } from "@/components/ui/button";
import { DefinitionList } from "@/components/ui/definition-list";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Section } from "@/components/ui/section";
import { Skeleton } from "@/components/ui/skeleton";
import type { GetNvkeOperatorJobPageQuery } from "@/graphql/__generated__/operations";
import type { NvkeOperatorArtifact } from "@/lib/nvke/operator-artifact";
import { MAX_OPERATOR_DOWNLOAD_BYTES, validOperatorArtifact } from "@/lib/nvke/operator-artifact";
import type { NvkeOperatorAgentAuthority } from "./NvkeOperatorAgentPanel";
import type { NvkeSiteView, NvkeOperationView } from "./types";

export type NvkeOperatorReceipt = Omit<
  NonNullable<GetNvkeOperatorJobPageQuery["astroliftNvkeOperatorJobPage"]["data"]>,
  "items" | "nextCursor" | "totalCount"
>;
export interface NvkeOperatorJobPanelProps {
  scopeKey: string;
  site: Pick<NvkeSiteView, "id" | "name" | "version" | "upstreamRevision">;
  operation: NvkeOperationView;
  authority: NvkeOperatorAgentAuthority | null;
  receipt: NvkeOperatorReceipt | null;
  table: CursorTableController<NvkeOperatorArtifact>;
  loaded: boolean;
  loading: boolean;
  canManage: boolean;
  canReconcile: boolean;
  canStop: boolean;
  pending: boolean;
  error: string | null;
  progress: { completed: number; total: number } | null;
  onLoad: () => void;
  onRefresh: () => void;
  onReconcile: () => Promise<boolean>;
  onStop: () => Promise<boolean>;
  onDownload: (artifact: NvkeOperatorArtifact, maximumBytes: number) => Promise<boolean>;
  onCancelDownload: () => void;
}
const budgetSchema = z.object({
  maximumMiB: z
    .number()
    .int()
    .min(1)
    .max(MAX_OPERATOR_DOWNLOAD_BYTES / 1048576),
});
export function NvkeOperatorJobPanel(props: NvkeOperatorJobPanelProps) {
  return (
    <JobControls
      key={JSON.stringify([
        props.scopeKey,
        props.site.id,
        props.site.version,
        props.operation.id,
        props.operation.version,
        props.operation.status,
        props.authority,
        props.receipt?.receiptSha256,
        props.canManage,
        props.canReconcile,
        props.canStop,
        props.pending,
      ])}
      {...props}
    />
  );
}
function JobControls(props: NvkeOperatorJobPanelProps) {
  const t = useTranslations("nvke"),
    j = useTranslations("nvke.operatorJob");
  const [review, setReview] = useState<
    | { kind: "download"; artifact: NvkeOperatorArtifact; maximumBytes: number }
    | { kind: "reconcile" }
    | { kind: "stop" }
    | null
  >(null);
  const [unconfirmed, setUnconfirmed] = useState(false);
  const form = useForm<{ maximumMiB: number }>({
    resolver: zodResolver(budgetSchema),
    defaultValues: { maximumMiB: 64 },
  });
  const allowed =
    props.canManage && !props.pending && !props.loading && !props.error && !unconfirmed;
  const list = useCursorTableList(props.table, {
    id: "nvke.operator.artifacts",
    label: j("artifacts"),
    searchPlaceholder: t("name"),
  });
  const columns: Column<NvkeOperatorArtifact>[] = [
    {
      id: "name",
      header: t("name"),
      cellClassName: "max-w-72",
      cell: (row) => (
        <code className="block truncate" title={row.name}>
          {row.name}
        </code>
      ),
    },
    { id: "size", header: j("size"), cell: (row) => <code>{row.size}</code> },
    {
      id: "sha256",
      header: "SHA-256",
      cellClassName: "max-w-56",
      cell: (row) => (
        <code className="block truncate" title={row.sha256}>
          {row.sha256}
        </code>
      ),
    },
    {
      id: "download",
      header: j("download"),
      cell: (row) => (
        <Button
          variant="outline"
          size="sm"
          disabled={!allowed || !props.receipt?.found}
          onClick={() => {
            void form.handleSubmit(({ maximumMiB }) => {
              if (!allowed) return;
              if (!validOperatorArtifact(row) || BigInt(row.size) > BigInt(maximumMiB * 1048576)) {
                form.setError("maximumMiB", { type: "artifactSize" });
                return;
              }
              setReview({
                kind: "download",
                artifact: { name: row.name, size: row.size, sha256: row.sha256 },
                maximumBytes: maximumMiB * 1048576,
              });
            })();
          }}
        >
          <DownloadIcon className="size-4" />
          {j("download")}
        </Button>
      ),
    },
  ];
  const receipt = props.receipt;
  return (
    <Section title={j("title")} description={j("help")}>
      <div className="flex flex-wrap gap-2">
        <Button
          variant="outline"
          disabled={props.pending || props.loading}
          onClick={() => {
            setUnconfirmed(false);
            props.onRefresh();
          }}
        >
          {t("refresh")}
        </Button>
        <Button variant="outline" disabled={!allowed || props.loaded} onClick={props.onLoad}>
          {j("load")}
        </Button>
        {props.operation.status === "UNCERTAIN" && (
          <Button
            variant="destructive"
            disabled={!allowed || !props.canStop}
            onClick={() => setReview({ kind: "stop" })}
          >
            {j("stop")}
          </Button>
        )}
        {props.operation.status === "UNCERTAIN" && (
          <Button
            disabled={!allowed || !props.canReconcile}
            onClick={() => setReview({ kind: "reconcile" })}
          >
            {j("reconcile")}
          </Button>
        )}
        {props.progress && (
          <Button variant="outline" disabled={!props.pending} onClick={props.onCancelDownload}>
            {j("cancelDownload")}
          </Button>
        )}
      </div>
      {props.error && (
        <p role="alert" className="text-danger-fg text-sm">
          {props.error}
        </p>
      )}
      {unconfirmed && !props.error && (
        <p role="alert" className="text-danger-fg text-sm">
          {t("requestFailed")}
        </p>
      )}
      {props.progress && (
        <p role="status" className="text-muted-foreground text-sm">
          {j("progress", { completed: props.progress.completed, total: props.progress.total })}
        </p>
      )}
      {props.loading && <Skeleton className="h-32" />}
      {!props.loaded && !props.loading && (
        <p className="text-muted-foreground text-sm">{j("unloaded")}</p>
      )}
      {receipt && (
        <DefinitionList
          items={[
            { term: j("receiptState"), description: <code>{receipt.state}</code> },
            { term: j("exitCode"), description: receipt.nativeExitCode ?? "—" },
            { term: t("errorCode"), description: <code>{receipt.errorCode ?? "—"}</code> },
            {
              term: j("effectful"),
              description: t(receipt.effectful ? "providers.yes" : "providers.no"),
            },
            {
              term: j("agentCancellation"),
              description: t(receipt.cancellationRequested ? "providers.yes" : "providers.no"),
            },
            {
              term: j("nativeRecovered"),
              description: t(
                receipt.reconciledFromNativeReceipt ? "providers.yes" : "providers.no"
              ),
            },
            {
              term: j("receiptDigest"),
              description: (
                <code className="text-xs [overflow-wrap:anywhere]">{receipt.receiptSha256}</code>
              ),
            },
            {
              term: t("agentInstance"),
              description: (
                <code className="text-xs [overflow-wrap:anywhere]">{receipt.instanceId}</code>
              ),
            },
            {
              term: t("agentBundle"),
              description: (
                <code className="text-xs [overflow-wrap:anywhere]">
                  {receipt.bundleManifestSha256}
                </code>
              ),
            },
            {
              term: t("revision"),
              description: (
                <code className="text-xs [overflow-wrap:anywhere]">{receipt.nativeRevision}</code>
              ),
            },
          ]}
        />
      )}
      {props.loaded && (
        <>
          <div className="max-w-sm space-y-2">
            <Label htmlFor="nvke-artifact-budget">{j("maximum")}</Label>
            <Input
              id="nvke-artifact-budget"
              type="number"
              min={1}
              max={256}
              disabled={!allowed}
              {...form.register("maximumMiB", { valueAsNumber: true })}
            />
            {form.formState.errors.maximumMiB && (
              <p role="alert" className="text-danger-fg text-sm">
                {form.formState.errors.maximumMiB.type === "artifactSize"
                  ? j("tooLarge")
                  : t("invalidForm")}
              </p>
            )}
          </div>
          <ListPage
            embedded
            list={list}
            rows={props.table.rows}
            label={j("artifacts")}
            loading={props.table.state === "loading"}
            stale={props.table.isStale}
            error={props.table.state === "error" ? { message: t("readFailed") } : null}
            onRetry={props.onRefresh}
            nextCursor={props.table.nextCursor}
            totalCount={props.table.totalCount}
            columns={columns}
            getRowId={(row) => row.name}
            empty={{ icon: <FileLockIcon />, title: j("empty") }}
          />
        </>
      )}
      <ConfirmDialog
        open={!!review}
        onOpenChange={(open) => {
          if (!open) setReview(null);
        }}
        title={j(
          review?.kind === "download" ? "download" : review?.kind === "stop" ? "stop" : "reconcile"
        )}
        confirmDisabled={
          !allowed ||
          (review?.kind === "reconcile" && !props.canReconcile) ||
          (review?.kind === "stop" && !props.canStop)
        }
        description={
          <>
            {j("review", {
              site: props.site.name,
              siteVersion: props.site.version,
              operation: props.operation.id,
              operationVersion: props.operation.version,
              authorityVersion: props.authority?.version ?? 0,
              instance: props.authority?.instanceId ?? "—",
              bundle: props.authority?.bundleManifestSha256 ?? "—",
              revision: props.site.upstreamRevision,
            })}
            <span className="mt-2 block text-xs [overflow-wrap:anywhere]">
              {t("action")}: {props.operation.operationKey} · {t("endpoint")}:{" "}
              {props.authority?.endpoint ?? "—"} · {j("receiptState")}: {receipt?.state ?? "—"}
            </span>
            <span className="mt-2 block">
              {j(
                review?.kind === "download"
                  ? "downloadHelp"
                  : review?.kind === "stop"
                    ? "stopHelp"
                    : "reconcileHelp"
              )}
            </span>
            {review?.kind === "download" && (
              <span className="mt-2 block font-mono text-xs [overflow-wrap:anywhere]">
                {review.artifact.name} · {review.artifact.size} · SHA-256 {review.artifact.sha256} ·{" "}
                {review.maximumBytes / 1048576} MiB
              </span>
            )}
          </>
        }
        onConfirm={async () => {
          if (
            !review ||
            !allowed ||
            (review.kind === "reconcile" && !props.canReconcile) ||
            (review.kind === "stop" && !props.canStop)
          )
            return false;
          try {
            const ok =
              review.kind === "download"
                ? await props.onDownload(review.artifact, review.maximumBytes)
                : review.kind === "stop"
                  ? await props.onStop()
                  : await props.onReconcile();
            if (!ok) setUnconfirmed(true);
            return ok;
          } catch {
            setUnconfirmed(true);
            return false;
          } finally {
            setReview(null);
          }
        }}
      />
    </Section>
  );
}
