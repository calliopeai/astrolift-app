"use client";

import Link from "next/link";
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
  return (
    <PageShell
      title="New pipeline"
      description="Create a pipeline in the active organization."
      actions={
        <Button variant="outline" asChild>
          <Link href="/pipelines">Back to pipelines</Link>
        </Button>
      }
    >
      {createdId ? (
        <div role="status" className="space-y-3">
          <p>Pipeline created.</p>
          <Button asChild>
            <Link href={`/pipelines/${encodeURIComponent(createdId)}`}>Open pipeline</Link>
          </Button>
        </div>
      ) : !allowed ? (
        <p role="status">
          You need permission to update apps in this organization to create a pipeline.
        </p>
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
              ["name", "Name", true],
              ["repoUrl", "Repository URL", true],
              ["defaultBranch", "Default branch", false],
              ["tomlPath", "TOML path", false],
            ] as const
          ).map(([field, label, required]) => (
            <div key={field} className="space-y-2">
              <Label htmlFor={`pipeline-${field}`}>{label}</Label>
              <Input
                id={`pipeline-${field}`}
                value={draft[field]}
                onChange={(event) => onChange(field, event.target.value)}
                required={required}
                disabled={saving}
              />
              {field === "repoUrl" && (
                <p className="text-muted-foreground text-sm">
                  Use the repository’s HTTPS or SSH address.
                </p>
              )}
              {field === "tomlPath" && (
                <p className="text-muted-foreground text-sm">
                  Leave blank to use the pipeline’s default configuration path.
                </p>
              )}
            </div>
          ))}
          {error && (
            <p role="alert" className="text-destructive text-sm">
              {error}
            </p>
          )}
          <Button type="submit" disabled={saving || !draft.name.trim() || !draft.repoUrl.trim()}>
            {saving ? "Creating…" : "Create pipeline"}
          </Button>
        </form>
      )}
    </PageShell>
  );
}
