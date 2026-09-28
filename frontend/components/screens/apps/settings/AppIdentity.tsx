"use client";

import { useTranslations } from "next-intl";
import * as React from "react";

import { SettingsSection } from "@/components/settings/SettingsPage";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

import type { useAppIdentity } from "./use-app-identity";

export type AppIdentityViewProps = ReturnType<typeof useAppIdentity>;

/**
 * The app's name and description, one SettingsSection that saves on its
 * own (spec 44 §5.3), with the slug, repository and default branch beside
 * them as read-only facts. A failed save keeps the edits and says why.
 */
export function AppIdentityView({
  name,
  description,
  slug,
  sourceRepo,
  sourceUrl,
  defaultBranch,
  saving,
  onSave,
}: AppIdentityViewProps) {
  const t = useTranslations("apps.settingsTab.identity");
  const id = React.useId();
  const [draft, setDraft] = React.useState({ name, description });
  const [error, setError] = React.useState<string | null>(null);

  // A refetch after someone else's save resets the fields to what is stored.
  const [stored, setStored] = React.useState({ name, description });
  if (stored.name !== name || stored.description !== description) {
    setStored({ name, description });
    setDraft({ name, description });
  }

  const dirty = draft.name !== name || draft.description !== description;
  const nameMissing = draft.name.trim() === "";

  async function save() {
    if (nameMissing) return;
    setError(null);
    try {
      await onSave(draft);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("toastFailed"));
    }
  }

  return (
    <SettingsSection
      title={t("title")}
      description={t("description")}
      dirty={dirty && !nameMissing}
      saving={saving}
      error={error}
      onSave={save}
      onCancel={() => {
        setDraft({ name, description });
        setError(null);
      }}
    >
      <div className="grid min-w-0 gap-2">
        <Label htmlFor={`${id}-name`}>{t("name")}</Label>
        <Input
          id={`${id}-name`}
          value={draft.name}
          onChange={(e) => setDraft((d) => ({ ...d, name: e.target.value }))}
          aria-invalid={nameMissing || undefined}
          aria-describedby={nameMissing ? `${id}-name-error` : undefined}
        />
        {nameMissing && (
          <p id={`${id}-name-error`} role="alert" className="text-danger text-xs">
            {t("nameRequired")}
          </p>
        )}
      </div>
      <div className="grid min-w-0 gap-2">
        <Label htmlFor={`${id}-description`}>{t("descriptionLabel")}</Label>
        <Textarea
          id={`${id}-description`}
          value={draft.description}
          rows={3}
          onChange={(e) => setDraft((d) => ({ ...d, description: e.target.value }))}
        />
      </div>
      <dl className="grid min-w-0 grid-cols-1 gap-x-8 gap-y-3 text-sm sm:grid-cols-3">
        <Fact label={t("slug")} value={slug} />
        <Fact
          label={t("repository")}
          value={
            sourceRepo ? (
              sourceUrl ? (
                <a
                  href={sourceUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="text-primary hover:underline"
                >
                  {sourceRepo}
                </a>
              ) : (
                sourceRepo
              )
            ) : (
              <span className="text-muted-foreground font-sans">{t("noRepository")}</span>
            )
          }
        />
        <Fact label={t("defaultBranch")} value={defaultBranch || "-"} />
      </dl>
    </SettingsSection>
  );
}

function Fact({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-muted-foreground text-xs tracking-wide uppercase">{label}</dt>
      <dd className="font-mono text-sm [overflow-wrap:anywhere]">{value}</dd>
    </div>
  );
}
