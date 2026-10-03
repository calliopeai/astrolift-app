"use client";
import { useId, useLayoutEffect, useRef, useState } from "react";
import { useFormatter, useTranslations } from "next-intl";
import { UploadIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ListPage } from "@/components/list/ListPage";
import type { LocalArtifactFieldsFragment } from "@/graphql/__generated__/operations";
import type { ModelPage } from "./ModelSubscriptionsPanel";
export type LocalImportPhase =
  | { kind: "idle" }
  | { kind: "hashing"; name: string; percent: number }
  | { kind: "uploading"; name: string; index: number; count: number }
  | { kind: "verifying" }
  | { kind: "failed"; message: string }
  | { kind: "canceled" }
  | { kind: "accepted" }
  | { kind: "verified"; artifact: LocalArtifactFieldsFragment; refreshFailed: boolean };
export interface LocalModelImportProps {
  scopeKey: string;
  allowed: boolean;
  page: ModelPage<LocalArtifactFieldsFragment>;
  phase: LocalImportPhase;
  onImport: (name: string, files: File[]) => Promise<void>;
  onCancel: () => void;
  onUseArtifact: (artifact: LocalArtifactFieldsFragment) => void;
}
export function LocalModelImportPanel(props: LocalModelImportProps) {
  return <ImportPanel key={props.scopeKey} {...props} />;
}
function ImportPanel(props: LocalModelImportProps) {
  const t = useTranslations("models.shared.localImport"),
    f = useFormatter(),
    id = useId();
  const [name, setName] = useState(""),
    [files, setFiles] = useState<File[]>([]);
  const input = useRef<HTMLInputElement>(null),
    phase = props.phase;
  const busy = ["hashing", "uploading", "verifying"].includes(phase.kind);
  const [previousPhase, setPreviousPhase] = useState(phase.kind);
  if (previousPhase !== phase.kind) {
    setPreviousPhase(phase.kind);
    if (phase.kind === "verified") setFiles([]);
  }
  useLayoutEffect(() => {
    if (phase.kind === "verified" && input.current) input.current.value = "";
  }, [phase.kind]);
  const select = (row: LocalArtifactFieldsFragment) => (
    <Button
      type="button"
      variant="outline"
      disabled={
        !props.allowed ||
        props.page.loading ||
        props.page.stale ||
        Boolean(props.page.error) ||
        row.state !== "verified"
      }
      onClick={() => props.onUseArtifact(row)}
    >
      {t("select")}
    </Button>
  );
  return (
    <section className="space-y-4">
      <h2 className="text-lg font-semibold">{t("title")}</h2>
      <p className="text-muted-foreground text-sm">{t("description")}</p>
      <form
        className="space-y-3 rounded-md border p-4"
        onSubmit={(event) => {
          event.preventDefault();
          if (!busy && props.allowed) void props.onImport(name, files);
        }}
      >
        <Label htmlFor={`${id}-name`}>{t("name")}</Label>
        <Input
          id={`${id}-name`}
          value={name}
          onChange={(event) => setName(event.target.value)}
          maxLength={128}
          required
          disabled={busy || !props.allowed}
        />
        <Label htmlFor={`${id}-files`}>{t("files")}</Label>
        <Input
          ref={input}
          id={`${id}-files`}
          type="file"
          multiple
          required
          disabled={busy || !props.allowed}
          onChange={(event) => setFiles(Array.from(event.target.files ?? []))}
        />
        <p className="text-muted-foreground text-sm">{t("limits")}</p>
        <Button type="submit" disabled={busy || !props.allowed || !name.trim() || !files.length}>
          {t("import")}
        </Button>
        {busy && (
          <Button type="button" variant="outline" onClick={props.onCancel}>
            {t("cancel")}
          </Button>
        )}
        {phase.kind === "hashing" && (
          <p role="status">
            {t("hashing", { name: phase.name, percent: f.number(phase.percent) })}
          </p>
        )}
        {phase.kind === "uploading" && (
          <p role="status">
            {t("uploading", { name: phase.name, index: phase.index, count: phase.count })}
          </p>
        )}
        {phase.kind === "verifying" && <p role="status">{t("verifying")}</p>}
        {phase.kind === "failed" && <p role="alert">{phase.message}</p>}
        {phase.kind === "canceled" && <p role="status">{t("canceled")}</p>}
        {phase.kind === "accepted" && <p role="status">{t("saved")}</p>}
      </form>
      {phase.kind === "verified" && (
        <div role="status" className="space-y-2 rounded-md border p-3">
          <p>{t("verified")}</p>
          <p>{phase.artifact.name}</p>
          <p className="font-mono text-xs break-all">{phase.artifact.manifestSha256}</p>
          {phase.refreshFailed && <p role="alert">{t("savedRefreshFailed")}</p>}
          <Button disabled={!props.allowed} onClick={() => props.onUseArtifact(phase.artifact)}>
            {t("select")}
          </Button>
        </div>
      )}
      <ListPage
        embedded
        {...props.page}
        label={t("title")}
        getRowId={(row) => row.id}
        empty={{ icon: <UploadIcon />, title: t("empty"), description: t("emptyDescription") }}
        columns={[
          {
            id: "name",
            header: t("sourceName"),
            cell: (row) => (
              <div>
                <p>{row.name}</p>
                <p className="text-muted-foreground text-xs">
                  {t("summary", { count: row.fileCount, bytes: f.number(Number(row.sizeBytes)) })}
                </p>
              </div>
            ),
          },
          {
            id: "state",
            header: t("state"),
            cell: (row) =>
              row.state === "verified"
                ? t("statusVerified")
                : row.state === "uploading"
                  ? t("statusUploading")
                  : row.state,
          },
          { id: "select", header: t("select"), cell: select },
        ]}
      />
    </section>
  );
}
