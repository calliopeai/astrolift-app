"use client";

/**
 * Form view for the app config tab (#1110): the schema-driven builder on the
 * left, a live TOML preview on the right (resizable split, mirroring
 * forms/new). TOML stays the source of truth — the parent owns `draft`; this
 * pane parses it into the model on entry and re-serializes on every edit.
 *
 * The model is a controlled bridge over `draft`: edits emit new TOML upward,
 * and an external draft change (sync-from-repo, conflict resolution) re-parses
 * and resets the model. If a manifest can't be safely round-tripped by the
 * scoped codec, the pane refuses to edit and points at the Code view — the raw
 * editor is never bypassed, so the operator is never worse off.
 */

import { AlertTriangleIcon, CheckCircle2Icon, CopyIcon, GripVerticalIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";
import { Panel, Group as PanelGroup, Separator as PanelResizeHandle } from "react-resizable-panels";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { tomlToModel, modelToToml, type ManifestModel } from "@/lib/manifest/model";
import { validateManifest } from "@/lib/manifest/schema";

import { ManifestFormBuilder } from "./manifest-form";

export function ManifestFormPane({
  draft,
  onDraftChange,
  onSwitchToCode,
}: {
  draft: string;
  onDraftChange: (toml: string) => void;
  onSwitchToCode: () => void;
}) {
  const t = useTranslations("apps.config.builder");
  const [parsed, setParsed] = React.useState(() => tomlToModel(draft));
  const lastEmitted = React.useRef<string | null>(null);

  // Re-sync when the draft changes from outside the form (initial load,
  // sync-from-repo, conflict adopt). A change the form itself emitted is a
  // no-op here — we compare against the exact text we last serialized.
  React.useEffect(() => {
    if (lastEmitted.current !== null && draft === lastEmitted.current) return;
    setParsed(tomlToModel(draft));
    lastEmitted.current = draft;
  }, [draft]);

  const errors = React.useMemo(
    () => (parsed.safe ? validateManifest(parsed.model) : []),
    [parsed]
  );
  const previewToml = React.useMemo(
    () => (parsed.safe ? modelToToml(parsed.model) : ""),
    [parsed]
  );

  const handleModelChange = React.useCallback(
    (model: ManifestModel) => {
      const toml = modelToToml(model);
      lastEmitted.current = toml;
      setParsed({ model, safe: true });
      onDraftChange(toml);
    },
    [onDraftChange]
  );

  const handleCopy = React.useCallback(() => {
    void navigator.clipboard?.writeText(previewToml);
    toast.success(t("copied"));
  }, [previewToml, t]);

  if (!parsed.safe) {
    return (
      <div className="border-warning-border bg-warning/10 flex flex-col gap-3 rounded-md border p-6 text-sm">
        <div className="flex items-center gap-2">
          <AlertTriangleIcon className="text-warning-fg size-4" />
          <span className="font-medium">{t("unsafeTitle")}</span>
        </div>
        <p className="text-muted-foreground">{t("unsafeBody", { reason: parsed.reason ?? "" })}</p>
        <Button variant="outline" size="sm" className="self-start" onClick={onSwitchToCode}>
          {t("switchToCode")}
        </Button>
      </div>
    );
  }

  return (
    <div className="h-[70vh] min-h-[520px] overflow-hidden rounded-lg border">
      <PanelGroup orientation="horizontal" className="h-full">
        <Panel defaultSize={62} minSize={35}>
          <div className="h-full overflow-y-auto p-5">
            <ManifestFormBuilder model={parsed.model} errors={errors} onChange={handleModelChange} />
          </div>
        </Panel>
        <PanelResizeHandle className="bg-border/50 hover:bg-border flex w-2 items-center justify-center transition-colors">
          <GripVerticalIcon className="text-muted-foreground size-4" />
        </PanelResizeHandle>
        <Panel defaultSize={38} minSize={25}>
          <div className="bg-muted/30 flex h-full flex-col">
            <div className="flex items-center justify-between border-b px-4 py-2.5">
              <div className="flex flex-col">
                <span className="text-sm font-medium">{t("previewTitle")}</span>
                <span className="text-muted-foreground text-2xs">{t("previewHint")}</span>
              </div>
              <Button variant="ghost" size="sm" className="h-7" onClick={handleCopy}>
                <CopyIcon className="size-3.5" /> {t("copy")}
              </Button>
            </div>
            {errors.length > 0 ? (
              <div className="border-destructive/40 bg-destructive/5 border-b px-4 py-2 text-xs">
                <div className="text-destructive flex items-center gap-1.5 font-medium">
                  <AlertTriangleIcon className="size-3.5" />
                  {t("invalidCount", { count: errors.length })}
                </div>
                <ul className="text-muted-foreground mt-1 list-inside list-disc space-y-0.5">
                  {errors.slice(0, 6).map((e, i) => (
                    <li key={i}>
                      <span className="font-mono text-2xs">{e.path}</span> — {e.message}
                    </li>
                  ))}
                  {errors.length > 6 && <li>{t("andMore", { count: errors.length - 6 })}</li>}
                </ul>
              </div>
            ) : (
              <div className="border-b px-4 py-2 text-xs text-success-fg">
                <span className="flex items-center gap-1.5">
                  <CheckCircle2Icon className="size-3.5" />
                  {t("validSummary", {
                    workloads: parsed.model.workloads.length,
                    services: parsed.model.managedServices.length,
                  })}
                </span>
              </div>
            )}
            <pre className="flex-1 overflow-auto p-4 font-mono text-xs leading-relaxed">
              {previewToml || t("emptyPreview")}
            </pre>
          </div>
        </Panel>
      </PanelGroup>
    </div>
  );
}
