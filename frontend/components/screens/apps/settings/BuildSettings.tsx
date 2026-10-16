"use client";

import { useTranslations } from "next-intl";
import * as React from "react";

import { SettingsSection } from "@/components/settings/SettingsPage";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

import type { useBuildSettings } from "./use-build-settings";

export type BuildSettingsViewProps = ReturnType<typeof useBuildSettings>;

/** Build modes with a label under `apps.settingsTab.build.modes`. */
const BUILD_MODES = new Set(["ci_pushed", "platform_build", "none"]);

/**
 * The app's build context and Dockerfile (astrolift-cli #159), one
 * SettingsSection that saves on its own, with the build mode and manifest
 * beside them as read-only facts. A failed save keeps the edits and says why;
 * a saved one says what still uses the previous paths.
 */
export function BuildSettingsView({
  buildContext,
  dockerfilePath,
  buildMode,
  manifestPath,
  hasRepository,
  slug,
  saving,
  onSave,
}: BuildSettingsViewProps) {
  const t = useTranslations("apps.settingsTab.build");
  const id = React.useId();
  const [draft, setDraft] = React.useState({ buildContext, dockerfilePath });
  const [error, setError] = React.useState<string | null>(null);
  const [saved, setSaved] = React.useState(false);

  // A refetch after someone else's save resets the fields to what is stored.
  const [stored, setStored] = React.useState({ buildContext, dockerfilePath });
  if (stored.buildContext !== buildContext || stored.dockerfilePath !== dockerfilePath) {
    setStored({ buildContext, dockerfilePath });
    setDraft({ buildContext, dockerfilePath });
  }

  const dirty = draft.buildContext !== buildContext || draft.dockerfilePath !== dockerfilePath;
  const contextMissing = draft.buildContext.trim() === "";
  const dockerfileMissing = draft.dockerfilePath.trim() === "";
  const edit = (patch: Partial<typeof draft>) => {
    setDraft((d) => ({ ...d, ...patch }));
    setSaved(false);
  };

  async function save() {
    if (contextMissing || dockerfileMissing) return;
    setError(null);
    try {
      await onSave(draft);
      setSaved(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("toastFailed"));
    }
  }

  // The managed CI workflow keeps the paths it was rendered with until it is
  // synced again; a platform build reads them at its next run. The command
  // skips the secrets step, which would rotate the deploy token.
  const savedNotice = !saved ? null : buildMode === "ci_pushed" && hasRepository ? (
    <>
      {t("resyncCi")}{" "}
      <code className="font-mono">astro ci setup {slug} --skip-webhook --skip-secrets</code>
    </>
  ) : buildMode === "platform_build" ? (
    t("platformBuild")
  ) : null;

  return (
    <SettingsSection
      title={t("title")}
      description={t("description")}
      dirty={dirty && !contextMissing && !dockerfileMissing}
      saving={saving}
      error={error}
      onSave={save}
      saveLabel={t("save")}
      onCancel={() => {
        setDraft({ buildContext, dockerfilePath });
        setError(null);
      }}
    >
      <div className="grid min-w-0 gap-4 sm:grid-cols-2">
        <div className="grid min-w-0 content-start gap-2">
          <Label htmlFor={`${id}-context`}>{t("buildContext")}</Label>
          <Input
            id={`${id}-context`}
            value={draft.buildContext}
            onChange={(e) => edit({ buildContext: e.target.value })}
            className="font-mono text-xs"
            aria-invalid={contextMissing || undefined}
            aria-describedby={contextMissing ? `${id}-context-error` : undefined}
          />
          {contextMissing && (
            <p id={`${id}-context-error`} role="alert" className="text-danger text-xs">
              {t("contextRequired")}
            </p>
          )}
        </div>
        <div className="grid min-w-0 content-start gap-2">
          <Label htmlFor={`${id}-dockerfile`}>{t("dockerfile")}</Label>
          <Input
            id={`${id}-dockerfile`}
            value={draft.dockerfilePath}
            onChange={(e) => edit({ dockerfilePath: e.target.value })}
            className="font-mono text-xs"
            aria-invalid={dockerfileMissing || undefined}
            aria-describedby={dockerfileMissing ? `${id}-dockerfile-error` : undefined}
          />
          {dockerfileMissing && (
            <p id={`${id}-dockerfile-error`} role="alert" className="text-danger text-xs">
              {t("dockerfileRequired")}
            </p>
          )}
        </div>
      </div>
      <p className="text-muted-foreground text-xs">{t("defaultsHelp")}</p>
      {buildMode === "none" && <p className="text-muted-foreground text-xs">{t("prebuilt")}</p>}
      {savedNotice && (
        <p
          role="status"
          className="border-info-border bg-info/10 rounded-md border p-3 text-sm [overflow-wrap:anywhere]"
        >
          {savedNotice}
        </p>
      )}
      <dl className="grid min-w-0 grid-cols-1 gap-x-8 gap-y-3 text-sm sm:grid-cols-2">
        <Fact
          label={t("buildMode")}
          value={BUILD_MODES.has(buildMode) ? t(`modes.${buildMode}`) : buildMode || "-"}
        />
        <Fact label={t("manifest")} value={manifestPath || "-"} mono />
      </dl>
    </SettingsSection>
  );
}

function Fact({ label, value, mono }: { label: string; value: React.ReactNode; mono?: boolean }) {
  return (
    <div className="min-w-0">
      <dt className="text-muted-foreground text-xs tracking-wide uppercase">{label}</dt>
      <dd className={mono ? "font-mono text-sm [overflow-wrap:anywhere]" : "text-sm"}>{value}</dd>
    </div>
  );
}
