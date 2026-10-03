"use client";

import Link from "next/link";
import { useTranslations } from "next-intl";
import { PageShell } from "@/components/PageShell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { CreatePipelineInput } from "@/graphql/__generated__/schema";

export type PipelineDraft = Required<CreatePipelineInput>;
export interface NewPipelineScreenProps {
  draft: PipelineDraft;
  onChange: (field: keyof PipelineDraft, value: string) => void;
  onSubmit: () => Promise<void>;
  saving: boolean;
  allowed: boolean;
  error: string | null;
  createdId: string | null;
}

/** The existing organization-owned CreatePipeline input, without new options. */
export function NewPipelineScreen({
  draft,
  onChange,
  onSubmit,
  saving,
  allowed,
  error,
  createdId,
}: NewPipelineScreenProps) {
  const t = useTranslations("PipelineUI");
  return (
    <PageShell
      title={t("new.title")}
      description={t("new.description")}
      actions={
        <Button variant="outline" asChild>
          <Link href="/pipelines">{t("new.back")}</Link>
        </Button>
      }
    >
      {createdId ? (
        <div role="status" className="space-y-3">
          <p>{t("new.created")}</p>
          <Button asChild>
            <Link href={`/pipelines/${encodeURIComponent(createdId)}`}>{t("new.open")}</Link>
          </Button>
        </div>
      ) : !allowed ? (
        <p role="status">{t("new.denied")}</p>
      ) : (
        <form
          className="max-w-2xl space-y-5"
          onSubmit={(event) => {
            event.preventDefault();
            void onSubmit();
          }}
        >
          {(
            [
              ["name", "name", true],
              ["repoUrl", "repositoryUrl", true],
              ["defaultBranch", "defaultBranch", false],
              ["tomlPath", "tomlPath", false],
            ] as const
          ).map(([field, label, required]) => (
            <div key={field} className="space-y-2">
              <Label htmlFor={`pipeline-${field}`}>{t(label)}</Label>
              <Input
                id={`pipeline-${field}`}
                value={draft[field]}
                onChange={(event) => onChange(field, event.target.value)}
                required={required}
                disabled={saving}
              />
              {field === "repoUrl" && (
                <p className="text-muted-foreground text-sm">{t("new.repositoryHint")}</p>
              )}
              {field === "tomlPath" && (
                <p className="text-muted-foreground text-sm">{t("new.pathHint")}</p>
              )}
            </div>
          ))}
          {error && (
            <p role="alert" className="text-destructive text-sm">
              {error}
            </p>
          )}
          <Button type="submit" disabled={saving || !draft.name.trim() || !draft.repoUrl.trim()}>
            {saving ? t("new.creating") : t("new.create")}
          </Button>
        </form>
      )}
    </PageShell>
  );
}
