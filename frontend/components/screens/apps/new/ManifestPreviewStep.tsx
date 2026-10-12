"use client";

import {
  AlertTriangleIcon,
  BotIcon,
  CheckCircle2Icon,
  FileTextIcon,
  FileXIcon,
  PencilIcon,
  RefreshCwIcon,
} from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import * as React from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { detectTomlSchema } from "@/lib/manifest/schema-detect";
import { cn } from "@/lib/utils";

export type ManifestFetchState = "idle" | "fetching" | "found" | "missing" | "error";

/** Client-side validation error keys, labelled under `appWizard.manifest.errors`. */
const MANIFEST_ERROR_KEYS = new Set(["empty", "name", "workloads"]);

/** The wizard-state fields this step reads and writes. */
export interface ManifestPreviewFields {
  manifestPath: string;
  defaultBranch: string;
  manifestRaw: string;
  manifestFromRepo: boolean;
  manifestValid: boolean;
  manifestErrors: string[];
  manifestLater: boolean;
}

export interface ManifestPreviewStepProps<S extends ManifestPreviewFields> {
  state: S;
  setState: React.Dispatch<React.SetStateAction<S>>;
  fetchState: ManifestFetchState;
  fetchError: string;
  editing: boolean;
  setEditing: (editing: boolean) => void;
  /** Re-fetch the manifest from the repo; the banner shows the result. */
  refetch: () => Promise<void>;
  /** The manifest holds server-masked env values this viewer can't reveal. */
  maskedEnvValues: boolean;
}

/**
 * Wizard step 2: manifest path, the fetched (or seeded) astrolift.toml, and
 * the "set up manifest later" escape hatch.
 */
export function ManifestPreviewStepView<S extends ManifestPreviewFields>({
  state,
  setState,
  fetchState,
  fetchError,
  editing,
  setEditing,
  refetch,
  maskedEnvValues,
}: ManifestPreviewStepProps<S>) {
  const t = useTranslations("appWizard.manifest");
  const code = (chunks: React.ReactNode) => <code className="font-mono">{chunks}</code>;
  const agentConfigDetected = detectTomlSchema(state.manifestRaw) === "agent_config";
  const isReadOnly = (fetchState === "found" && !editing) || state.manifestLater;

  return (
    <div className="flex flex-col gap-4">
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="space-y-2">
          <Label htmlFor="manifest-path">{t("pathLabel")}</Label>
          <Input
            id="manifest-path"
            value={state.manifestPath}
            onChange={(e) => setState((s) => ({ ...s, manifestPath: e.target.value }))}
            className="font-mono text-xs"
          />
          <p className="text-muted-foreground text-xs">{t.rich("pathHelp", { code })}</p>
        </div>
        <div className="space-y-2">
          <Label htmlFor="default-branch">{t("branchLabel")}</Label>
          <Input
            id="default-branch"
            value={state.defaultBranch}
            onChange={(e) => setState((s) => ({ ...s, defaultBranch: e.target.value }))}
            className="font-mono text-xs"
          />
        </div>
      </div>

      <FetchBanner state={fetchState} error={fetchError} onRetry={refetch} />

      <label className="flex items-start gap-3 rounded-md border p-4">
        <input
          type="checkbox"
          className="mt-1"
          checked={state.manifestLater}
          onChange={(e) => setState((s) => ({ ...s, manifestLater: e.target.checked }))}
        />
        <div className="flex-1">
          <span className="font-medium">{t("laterTitle")}</span>
          <p className="text-muted-foreground mt-1 text-xs">
            {t.rich("laterHelp", {
              code,
              b: (chunks) => <span className="font-medium">{chunks}</span>,
            })}
          </p>
        </div>
      </label>

      <div className={cn("space-y-2", state.manifestLater && "opacity-60")}>
        <div className="flex items-center justify-between">
          <Label htmlFor="manifest-raw">{t("label")}</Label>
          <div className="flex items-center gap-1">
            {fetchState === "found" && !editing && (
              <Button type="button" size="sm" variant="ghost" onClick={() => setEditing(true)}>
                <PencilIcon className="size-3.5" />
                {t("edit")}
              </Button>
            )}
            <Button
              type="button"
              size="sm"
              variant="ghost"
              onClick={() => void refetch()}
              disabled={fetchState === "fetching"}
              title={t("refetchTitle")}
            >
              <RefreshCwIcon
                className={cn("size-3.5", fetchState === "fetching" && "animate-spin")}
              />
              {fetchState === "fetching" ? t("fetching") : t("refetch")}
            </Button>
          </div>
        </div>
        <Textarea
          id="manifest-raw"
          value={state.manifestRaw}
          readOnly={isReadOnly}
          onChange={(e) => {
            setState((s) => ({
              ...s,
              manifestRaw: e.target.value,
              manifestFromRepo: false,
            }));
          }}
          rows={16}
          className={cn("font-mono text-xs", isReadOnly && "bg-muted/40 cursor-default")}
          placeholder={t("placeholder")}
        />
        {state.manifestFromRepo && maskedEnvValues && (
          <p className="text-muted-foreground text-xs">{t("maskedEnv")}</p>
        )}
        {agentConfigDetected && !state.manifestLater && (
          <div className="border-info-border bg-info/10 flex flex-col gap-2 rounded-md border p-3 text-sm">
            <div className="flex items-center gap-2">
              <BotIcon className="text-info-fg size-4" />
              <span className="font-medium">{t("agentConfigTitle")}</span>
            </div>
            <p className="text-muted-foreground text-xs">{t.rich("agentConfigBody", { code })}</p>
            <div className="flex flex-wrap gap-2">
              <Button asChild size="sm" variant="outline">
                <Link href="/agents/new">{t("onboardFleet")}</Link>
              </Button>
              <Button asChild size="sm" variant="outline">
                <Link href="/agents/skills/import">{t("importSkills")}</Link>
              </Button>
            </div>
            <p className="text-muted-foreground text-2xs">
              {t.rich("agentConfigLater", {
                b: (chunks) => <span className="font-medium">{chunks}</span>,
              })}
            </p>
          </div>
        )}
        {state.manifestErrors.length > 0 && (
          <ul className="text-destructive list-inside list-disc space-y-1 text-xs">
            {state.manifestErrors.map((err, i) => (
              <li key={i}>{MANIFEST_ERROR_KEYS.has(err) ? t(`errors.${err}`) : err}</li>
            ))}
          </ul>
        )}
        {state.manifestValid && (
          <p className="text-success-fg inline-flex items-center gap-1 text-xs">
            <CheckCircle2Icon className="size-3.5" />
            {t.rich("valid", { code })}
          </p>
        )}
      </div>
      {state.manifestLater && (
        <p className="text-muted-foreground inline-flex items-center gap-1 text-xs">
          <CheckCircle2Icon className="text-info-fg size-3.5" />
          {t("skipped")}
        </p>
      )}
    </div>
  );
}

function FetchBanner({
  state,
  error,
  onRetry,
}: {
  state: ManifestFetchState;
  error: string;
  onRetry: () => void;
}) {
  const t = useTranslations("appWizard.manifest");
  if (state === "idle" || state === "fetching") return null;
  if (state === "found") {
    return (
      <div className="border-success-border bg-success/10 flex items-center gap-2 rounded-md border p-3 text-sm">
        <CheckCircle2Icon className="text-success-fg size-4" />
        <span className="flex-1">{t("loaded")}</span>
        <Badge variant="secondary" className="gap-1">
          <FileTextIcon className="size-3" /> {t("fromRepo")}
        </Badge>
      </div>
    );
  }
  if (state === "missing") {
    return (
      <div className="border-warning-border bg-warning/10 flex items-center gap-2 rounded-md border p-3 text-sm">
        <FileXIcon className="text-warning-fg size-4" />
        <span className="flex-1">{t("missing")}</span>
      </div>
    );
  }
  return (
    <div className="border-destructive/40 bg-destructive/10 flex items-center gap-2 rounded-md border p-3 text-sm">
      <AlertTriangleIcon className="text-destructive size-4" />
      <span className="flex-1">{t("fetchFailed", { error })}</span>
      <Button type="button" size="sm" variant="outline" onClick={onRetry}>
        {t("retry")}
      </Button>
    </div>
  );
}
